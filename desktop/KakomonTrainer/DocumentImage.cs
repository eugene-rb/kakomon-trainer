using OpenCvSharp;

namespace KakomonTrainer;

public static class DocumentImage
{
    // Run once on the aligned page, before cropping. Geometry and colour channels
    // remain intact so OCR boxes and annotations use the same coordinates.
    public static byte[] Enhance(Mat aligned)
    {
        using var lab = new Mat();
        Cv2.CvtColor(aligned, lab, ColorConversionCodes.BGR2Lab);
        using var light = new Mat();
        Cv2.ExtractChannel(lab, light, 0);

        // Estimate paper illumination at reduced resolution. Closing fills thin
        // ink strokes; division removes broad shadows without thresholding ink.
        var scale = Math.Min(1.0, 1200.0 / Math.Max(light.Width, light.Height));
        using var small = new Mat();
        Cv2.Resize(light, small, new Size(Math.Max(1, (int)(light.Width * scale)), Math.Max(1, (int)(light.Height * scale))), interpolation: InterpolationFlags.Area);
        using var kernel = Cv2.GetStructuringElement(MorphShapes.Ellipse, new Size(41, 41));
        using var backgroundSmall = new Mat();
        Cv2.MorphologyEx(small, backgroundSmall, MorphTypes.Close, kernel);
        Cv2.GaussianBlur(backgroundSmall, backgroundSmall, new Size(0, 0), 7);
        using var background = new Mat();
        Cv2.Resize(backgroundSmall, background, light.Size(), interpolation: InterpolationFlags.Linear);
        using var backgroundFloat = new Mat();
        background.ConvertTo(backgroundFloat, MatType.CV_32FC1);
        // Do not amplify nearly black regions (filled diagrams or missing data).
        using var floor = new Mat(backgroundFloat.Size(), MatType.CV_32FC1, new Scalar(40));
        Cv2.Max(backgroundFloat, floor, backgroundFloat);
        using var lightFloat = new Mat();
        light.ConvertTo(lightFloat, MatType.CV_32FC1);
        using var correctedFloat = new Mat();
        Cv2.Divide(lightFloat, backgroundFloat, correctedFloat, 245);
        using var corrected = new Mat();
        correctedFloat.ConvertTo(corrected, MatType.CV_8UC1);

        // Bounded local contrast and a gentle monotonic S curve keep faint pencil
        // strokes and anti-aliased edges instead of turning them into binary pixels.
        using var clahe = Cv2.CreateCLAHE(1.5, new Size(8, 8));
        using var local = new Mat();
        clahe.Apply(corrected, local);
        Cv2.AddWeighted(corrected, 0.75, local, 0.25, 0, corrected);
        using var curve = new Mat(1, 256, MatType.CV_8UC1);
        for (var i = 0; i < 256; i++)
        {
            var x = i / 255.0;
            curve.Set(0, i, (byte)Math.Round(255 * (0.65 * x + 0.35 * x * x * (3 - 2 * x))));
        }
        Cv2.LUT(corrected, curve, corrected);
        Cv2.InsertChannel(corrected, lab, 0);
        using var output = new Mat();
        Cv2.CvtColor(lab, output, ColorConversionCodes.Lab2BGR);
        return output.ToBytes(".png");
    }
}
