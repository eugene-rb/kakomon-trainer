using System.IO;
using OpenCvSharp;

namespace KakomonTrainer;

public sealed class ScanService(DataStore store, PdfService pdf, AiService ai)
{
    public async Task<Session> ScanAsync(Template template, string[] paths, bool useOcr, IProgress<string> progress, CancellationToken token)
    {
        if (template.Questions.Count == 0 || template.Questions.Any(q => q.Regions.Count == 0)) throw new InvalidDataException("先に全設問の解答領域を指定して保存してください。");
        var session = new Session { SessionId = DateTime.Now.ToString("yyyyMMdd-HHmmss") + "-" + Guid.NewGuid().ToString("N")[..6], TemplateId = template.TemplateId, OcrBackend = useOcr ? ai.OcrBackend : "manual" };
        var dir = store.SessionDir(session.SessionId); Directory.CreateDirectory(Path.Combine(dir, "normalized")); Directory.CreateDirectory(Path.Combine(dir, "crops")); store.SaveSession(session);
        DataStore.Write(Path.Combine(dir, "template-snapshot.json"), template);
        try
        {
            var index = 0;
            foreach (var path in paths)
            {
                progress.Report($"答案を読み込み中: {Path.GetFileName(path)}");
                var images = await pdf.RenderAsync(path, template.Dpi, token);
                foreach (var bytes in images)
                {
                    token.ThrowIfCancellationRequested(); progress.Report($"{index + 1}枚目を位置合わせ中…");
                    var aligned = await Task.Run(() => Align(bytes, template), token);
                    if (session.Pages.Any(p => p.PageNo == aligned.Page.PageNo)) throw new InvalidDataException($"{aligned.Page.PageNo}ページが重複しています。答案1組ずつ取り込んでください。");
                    var filename = $"page_{aligned.Page.PageNo}.png";
                    await File.WriteAllBytesAsync(DataStore.Child(Path.Combine(dir, "normalized"), filename), aligned.Bytes, token);
                    session.Pages.Add(new PageRecord { PageNo = aligned.Page.PageNo, SourceIndex = index++, TemplateId = template.TemplateId, NormalizedFilename = filename, QrDetected = true, QrPayload = aligned.Payload, MarkerIds = [0, 1, 3], AlignmentErrorPx = aligned.Error });
                    if (aligned.Error > 10) session.Warnings.Add($"{aligned.Page.PageNo}ページ: 位置合わせ残差 {aligned.Error:F1}px。切り出し画像を確認してください。");
                }
            }
            var missing = template.Questions.SelectMany(q => q.Regions).Select(r => r.PageNo).Distinct().Except(session.Pages.Select(p => p.PageNo)).ToArray();
            if (missing.Length > 0) throw new InvalidDataException($"必要なページがありません: {string.Join(", ", missing)}");
            foreach (var q in template.Questions)
            {
                var texts = new List<string>();
                for (var i = 0; i < q.Regions.Count; i++)
                {
                    token.ThrowIfCancellationRequested(); var r = q.Regions[i]; var record = session.Pages.Single(p => p.PageNo == r.PageNo);
                    using var image = Cv2.ImRead(store.NormalizedPath(session, record));
                    var x0 = Math.Max(0, (int)Math.Floor(r.Rect[0] * image.Width) - 8); var y0 = Math.Max(0, (int)Math.Floor(r.Rect[1] * image.Height) - 8);
                    var x1 = Math.Min(image.Width, (int)Math.Ceiling(r.Rect[2] * image.Width) + 8); var y1 = Math.Min(image.Height, (int)Math.Ceiling(r.Rect[3] * image.Height) + 8);
                    using var cropped = new Mat(image, new Rect(x0, y0, x1 - x0, y1 - y0));
                    var crop = new Crop { QuestionId = q.Id, Index = i, PageNo = r.PageNo, RegionRect = r.Rect, CropOriginCanvas = [x0, y0], CropW = x1 - x0, CropH = y1 - y0 };
                    var imageBytes = cropped.ToBytes(".png"); await File.WriteAllBytesAsync(DataStore.Child(Path.Combine(dir, "crops"), crop.Filename), imageBytes, token);
                    if (useOcr)
                    {
                        progress.Report($"{q.Id} の文字を読み取り中…");
                        try { var ocr = await ai.OcrAsync(imageBytes, token); crop.OcrText = ocr.Text; crop.OcrWords = ocr.Words; }
                        catch (Exception ex) when (ex is not OperationCanceledException) { crop.OcrError = ex.Message; session.Warnings.Add($"{q.Id}: OCRに失敗。画像を確認して転記してください。{ex.Message}"); }
                    }
                    session.Crops.Add(crop); texts.Add(crop.OcrText);
                }
                session.Transcriptions.Add(new Transcription { QuestionId = q.Id, TranscriptionText = string.Join("\n", texts) });
            }
            session.Status = "ready"; store.SaveSession(session); return session;
        }
        catch (Exception ex) { session.Status = "failed"; session.Error = ex is OperationCanceledException ? "取り込みを中止しました。" : ex.Message; store.SaveSession(session); throw; }
    }
    public static (PageInfo Page, byte[] Bytes, string Payload, double Error) Align(byte[] bytes, Template template)
    {
        using var source = Cv2.ImDecode(bytes, ImreadModes.Color);
        if (source.Empty()) throw new InvalidDataException("答案画像を読み込めません。");
        var markers = PdfService.DetectMarkers(source);
        if (!new[] { 0, 1, 3 }.All(markers.ContainsKey)) throw new InvalidDataException("位置合わせマーカーが不足しています。マーカー付き用紙を四隅までスキャンしてください。");
        // Try each page's measured geometry to decode QR after a coarse affine transform.
        foreach (var candidate in template.Pages)
        {
            var src = new[] { 0, 1, 3 }.Select(id => markers[id]).ToArray();
            var dst = new[] { 0, 1, 3 }.Select(id => { var m = candidate.Markers.Single(m => m.Id == id); return new Point2f((float)m.Cx, (float)m.Cy); }).ToArray();
            using var affine = Cv2.GetAffineTransform(src, dst); using var coarse = new Mat();
            Cv2.WarpAffine(source, coarse, affine, new Size(candidate.CanvasW, candidate.CanvasH), borderMode: BorderTypes.Constant, borderValue: Scalar.White);
            var qr = PdfService.DetectQr(coarse);
            if (qr.Payload != $"MG1|{template.TemplateId}|{candidate.PageNo}" || qr.Quad.Length != 4) continue;
            if (candidate.QrQuad.Count != 4) throw new InvalidDataException("用紙のQR座標がありません。元PDFから再登録してください。");
            using var inverse = new Mat(); Cv2.InvertAffineTransform(affine, inverse);
            Point2d Undo(Point2f p) => new(inverse.At<double>(0, 0) * p.X + inverse.At<double>(0, 1) * p.Y + inverse.At<double>(0, 2), inverse.At<double>(1, 0) * p.X + inverse.At<double>(1, 1) * p.Y + inverse.At<double>(1, 2));
            var allSrc = src.Select(p => new Point2d(p.X, p.Y)).Concat(qr.Quad.Select(Undo)).ToArray();
            var allDst = dst.Select(p => new Point2d(p.X, p.Y)).Concat(candidate.QrQuad.Select(p => new Point2d(p[0], p[1]))).ToArray();
            using var homography = Cv2.FindHomography(allSrc, allDst); using var normalized = new Mat();
            Cv2.WarpPerspective(source, normalized, homography, new Size(candidate.CanvasW, candidate.CanvasH), borderMode: BorderTypes.Constant, borderValue: Scalar.White);
            var actual = PdfService.DetectMarkers(normalized);
            if (!new[] { 0, 1, 3 }.All(actual.ContainsKey)) throw new InvalidDataException("位置合わせ後にマーカーを確認できません。");
            var error = candidate.Markers.Where(m => actual.ContainsKey(m.Id)).Max(m => Math.Sqrt(Math.Pow(m.Cx - actual[m.Id].X, 2) + Math.Pow(m.Cy - actual[m.Id].Y, 2)));
            return (candidate, normalized.ToBytes(".png"), qr.Payload, error);
        }
        throw new InvalidDataException("QRが読み取れないか、選択した用紙と一致しません。用紙の選択とスキャン品質を確認してください。");
    }
}
