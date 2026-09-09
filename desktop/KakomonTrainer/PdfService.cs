using System.IO;
using System.Windows.Media.Imaging;
using OpenCvSharp;
using OpenCvSharp.Aruco;
using PdfSharp.Drawing;
using PdfSharp.Pdf;
using QRCoder;
using Docnet.Core;
using Docnet.Core.Models;

namespace KakomonTrainer;

public sealed class PdfService
{
    static PdfService() { PdfSharp.Fonts.GlobalFontSettings.FontResolver ??= new PdfFonts(); }
    public async Task<List<byte[]>> RenderAsync(string path, int dpi, CancellationToken token = default)
    {
        if (!path.EndsWith(".pdf", StringComparison.OrdinalIgnoreCase))
        {
            var bytes = await File.ReadAllBytesAsync(path, token);
            using var input = new MemoryStream(bytes);
            var decoder = BitmapDecoder.Create(input, BitmapCreateOptions.PreservePixelFormat, BitmapCacheOption.OnLoad);
            var frames = new List<byte[]>();
            foreach (var frame in decoder.Frames)
            {
                token.ThrowIfCancellationRequested(); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(frame);
                using var output = new MemoryStream(); encoder.Save(output); frames.Add(output.ToArray());
            }
            return frames;
        }
        return await Task.Run(() =>
        {
            // CPU rendering avoids loading the GPU PDF renderer into the WPF process.
            using var document = DocLib.Instance.GetDocReader(Path.GetFullPath(path), new PageDimensions(dpi / 72d));
            var pages = new List<byte[]>();
            for (var i = 0; i < document.GetPageCount(); i++)
            {
                token.ThrowIfCancellationRequested(); using var page = document.GetPageReader(i);
                var bytes = page.GetImage(RenderFlags.RenderAnnotations);
                // PDFium renders into BGRA with a transparent background; composite on paper white.
                for (var offset = 0; offset < bytes.Length; offset += 4)
                {
                    var alpha = bytes[offset + 3];
                    for (var channel = 0; channel < 3; channel++) bytes[offset + channel] = (byte)((bytes[offset + channel] * alpha + 255 * (255 - alpha)) / 255);
                    bytes[offset + 3] = 255;
                }
                using var image = Mat.FromPixelData(page.GetPageHeight(), page.GetPageWidth(), MatType.CV_8UC4, bytes);
                pages.Add(image.ToBytes(".png"));
            }
            return pages;
        }, token);
    }
    public static BitmapImage Bitmap(byte[] bytes)
    {
        using var stream = new MemoryStream(bytes);
        var image = new BitmapImage(); image.BeginInit(); image.CacheOption = BitmapCacheOption.OnLoad; image.StreamSource = stream; image.EndInit(); image.Freeze(); return image;
    }
    public static Dictionary<int, Point2f> DetectMarkers(Mat image)
    {
        using var dictionary = CvAruco.GetPredefinedDictionary(PredefinedDictionaryType.Dict4X4_50);
        using var detector = new ArucoDetector(dictionary);
        detector.DetectMarkers(image, out var corners, out var ids, out _);
        return ids.Select((id, i) => (id, point: new Point2f(corners[i].Average(p => p.X), corners[i].Average(p => p.Y)))).GroupBy(p => p.id).ToDictionary(g => g.Key, g => g.First().point);
    }
    public static (string Payload, Point2f[] Quad) DetectQr(Mat image)
    {
        using var detector = new QRCodeDetector();
        var payload = detector.DetectAndDecode(image, out var points);
        if (points is { Length: 4 }) return (payload, points);
        // 300dpiでは1モジュールが5px前後しかなく、モジュール境界が非整数ピクセルに
        // 落ちる並びのときだけ検出器がファインダパターンを見失う。2倍に拡大すると
        // 実測した取りこぼし9件のうち8件が復帰したため、失敗時のみ再試行する。
        using var gray = new Mat();
        if (image.Channels() == 1) image.CopyTo(gray); else Cv2.CvtColor(image, gray, ColorConversionCodes.BGR2GRAY);
        using var scaled = new Mat();
        Cv2.Resize(gray, scaled, new Size(), 2, 2, InterpolationFlags.Cubic);
        var retried = detector.DetectAndDecode(scaled, out var scaledPoints);
        if (scaledPoints is not { Length: 4 }) return (payload, points ?? []);
        return (retried, scaledPoints.Select(p => new Point2f(p.X / 2f, p.Y / 2f)).ToArray());
    }
    public static PageInfo Measure(byte[] image, int pageNo)
    {
        using var mat = Cv2.ImDecode(image, ImreadModes.Color);
        var markers = DetectMarkers(mat); var qr = DetectQr(mat);
        return new PageInfo { PageNo = pageNo, CanvasW = mat.Width, CanvasH = mat.Height, Markers = markers.Select(m => new Marker { Id = m.Key, Cx = m.Value.X, Cy = m.Value.Y }).ToList(), QrQuad = qr.Quad.Select(p => new double[] { p.X, p.Y }).ToList() };
    }
    public async Task<Template> RegisterAsync(DataStore store, string source, string title, CancellationToken token)
    {
        var id = "sheet-" + Guid.NewGuid().ToString("N")[..12]; var dir = store.TemplateDir(id);
        Directory.CreateDirectory(Path.Combine(dir, "refs"));
        try
        {
            File.Copy(source, Path.Combine(dir, "source.pdf"));
            await Task.Run(() => BuildBlank(source, Path.Combine(dir, "blank.pdf"), id), token);
            var images = await RenderAsync(Path.Combine(dir, "blank.pdf"), 300, token);
            var template = new Template { TemplateId = id, Title = title };
            for (var i = 0; i < images.Count; i++)
            {
                var info = await Task.Run(() => Measure(images[i], i + 1), token);
                // マーカーとQRは失敗時の対処が違うので、どちらを読み取れなかったかを分けて伝える。
                var missing = new[] { 0, 1, 3 }.Where(mid => !info.Markers.Any(m => m.Id == mid)).ToArray();
                if (missing.Length > 0) throw new InvalidDataException($"{i + 1}ページのマーカー（{string.Join("・", missing)}）を読み取れません。用紙の四隅の余白を確認してください。");
                if (info.QrQuad.Count != 4) throw new InvalidDataException($"{i + 1}ページのQRコードを読み取れません。用紙右下の余白と印刷の解像度を確認してください。");
                template.Pages.Add(info);
            }
            store.SaveTemplate(template); return template;
        }
        catch { Directory.Delete(dir, true); throw; } // Newly allocated directory only.
    }
    private static void BuildBlank(string source, string output, string id)
    {
        using var form = XPdfForm.FromFile(source); using var pdf = new PdfDocument();
        using var dictionary = CvAruco.GetPredefinedDictionary(PredefinedDictionaryType.Dict4X4_50);
        for (var i = 1; i <= form.PageCount; i++)
        {
            form.PageNumber = i; var page = pdf.AddPage(); page.Width = XUnit.FromPoint(form.PointWidth); page.Height = XUnit.FromPoint(form.PointHeight);
            using var g = XGraphics.FromPdfPage(page); var w = page.Width.Point; var h = page.Height.Point;
            g.DrawImage(form, w * .025, h * .025, w * .95, h * .95);
            var size = 12 * 72 / 25.4; var margin = 8 * 72 / 25.4;
            foreach (var mid in new[] { 0, 1, 3 })
            {
                using var marker = new Mat(); dictionary.GenerateImageMarker(mid, 300, marker, 1);
                using var padded = new Mat(); Cv2.CopyMakeBorder(marker, padded, 75, 75, 75, 75, BorderTypes.Constant, Scalar.White);
                using var stream = new MemoryStream(padded.ToBytes(".png")); using var image = XImage.FromStream(stream);
                var x = mid == 1 ? w - margin - size : margin; var y = mid == 3 ? h - margin - size : margin;
                g.DrawImage(image, x - size * .25, y - size * .25, size * 1.5, size * 1.5);
            }
            using var qr = QRCodeGenerator.GenerateQrCode($"MG1|{id}|{i}", QRCodeGenerator.ECCLevel.M);
            using var png = new PngByteQRCode(qr); using var qrStream = new MemoryStream(png.GetGraphic(10)); using var qrImage = XImage.FromStream(qrStream);
            var qs = 15 * 72 / 25.4; g.DrawImage(qrImage, w - margin - qs, h - margin - qs, qs, qs);
        }
        pdf.Save(output);
    }
    public async Task ExportGradedAsync(DataStore store, Template t, Session s, GradeResult result, string path, CancellationToken token)
    {
        await Task.Run(() =>
        {
            using var pdf = new PdfDocument(); var font = new XFont("Kakomon Japanese", 10); var red = XBrushes.Red;
            foreach (var record in s.Pages.OrderBy(p => p.PageNo))
            {
                token.ThrowIfCancellationRequested(); var info = t.Pages.Single(p => p.PageNo == record.PageNo);
                var page = pdf.AddPage(); page.Width = XUnit.FromPoint(info.CanvasW * 72d / t.Dpi); page.Height = XUnit.FromPoint(info.CanvasH * 72d / t.Dpi);
                using var g = XGraphics.FromPdfPage(page);
                using var image = XImage.FromFile(store.NormalizedPath(s, record));
                g.DrawImage(image, 0, 0, page.Width.Point, page.Height.Point);
                foreach (var q in t.Questions)
                {
                    var qr = result.Questions.SingleOrDefault(r => r.Id == q.Id); if (qr == null) continue;
                    foreach (var region in q.Regions.Where(r => r.PageNo == record.PageNo))
                    {
                        var x = region.Rect[0] * page.Width.Point; var y = region.Rect[1] * page.Height.Point;
                        g.DrawRectangle(new XPen(XColors.Red, .7), x, y, (region.Rect[2] - region.Rect[0]) * page.Width.Point, (region.Rect[3] - region.Rect[1]) * page.Height.Point);
                        g.DrawString($"{q.Id}: {qr.Score?.ToString() ?? "要確認"}/{q.MaxScore}", font, red, x, Math.Max(12, y - 3));
                    }
                    foreach (var crop in s.Crops.Where(c => c.QuestionId == q.Id && c.PageNo == record.PageNo))
                    {
                        var combined = string.Concat(crop.OcrWords.Select(word => AnswerMatcher.Normalize(word.Text)));
                        foreach (var issue in qr.Issues.Where(issue => !string.IsNullOrWhiteSpace(issue.Quote)))
                        {
                            var quote = AnswerMatcher.Normalize(issue.Quote); if (quote.Length == 0) continue;
                            var at = combined.IndexOf(quote, StringComparison.Ordinal); if (at < 0) continue;
                            var offset = 0;
                            foreach (var word in crop.OcrWords)
                            {
                                var end = offset + AnswerMatcher.Normalize(word.Text).Length;
                                if (offset < at + quote.Length && end > at && word.Bbox.Length == 4)
                                {
                                    var factor = 72d / t.Dpi;
                                    g.DrawLine(new XPen(XColors.Red, 1), (crop.CropOriginCanvas[0] + word.Bbox[0]) * factor, (crop.CropOriginCanvas[1] + word.Bbox[3]) * factor, (crop.CropOriginCanvas[0] + word.Bbox[2]) * factor, (crop.CropOriginCanvas[1] + word.Bbox[3]) * factor);
                                }
                                offset = end;
                            }
                        }
                    }
                }
            }
            PdfPage? notePage = null; XGraphics? notes = null; double lineY = 0;
            try
            {
                foreach (var line in ReviewText(result).Split('\n'))
                {
                    // Conservative character wrapping supports Japanese without depending on spaces.
                    var remaining = line.TrimEnd('\r');
                    do
                    {
                        if (notes == null || lineY > notePage!.Height.Point - 40)
                        { notes?.Dispose(); notePage = pdf.AddPage(); notes = XGraphics.FromPdfPage(notePage); lineY = 40; }
                        var length = Math.Min(48, remaining.Length); var part = remaining[..length];
                        notes.DrawString(part, font, XBrushes.DarkRed, 35, lineY); lineY += 17; remaining = remaining[length..];
                    } while (remaining.Length > 0);
                }
            }
            finally { notes?.Dispose(); }
            pdf.Save(path);
        }, token);
    }
    public static string ReviewText(GradeResult result) => $"採点結果  {result.GradedAt:g}\n合計 {result.TotalScore} / {result.TotalMaxScore}\n\n" + string.Join("\n\n", result.Questions.Select(q => $"{q.Id}  {q.Score?.ToString() ?? "要確認"} / {q.MaxScore}\n{q.Feedback}\n" + string.Join("\n", q.Issues.Select(i => $"[{i.Tag}] -{i.Deduction}  {i.Quote}\n{i.Comment}"))));
}
