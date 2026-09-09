using System.IO;
using PdfSharp.Fonts;

namespace KakomonTrainer;

public sealed class PdfFonts : IFontResolver
{
    public FontResolverInfo? ResolveTypeface(string familyName, bool bold, bool italic) => familyName == "Kakomon Japanese" ? new FontResolverInfo("BIZUDGothic") : PlatformFontResolver.ResolveTypeface(familyName, bold, italic);
    public byte[]? GetFont(string faceName) => faceName == "BIZUDGothic" ? File.ReadAllBytes(Path.Combine(AppContext.BaseDirectory, "Assets", "Fonts", "BIZUDGothic-Regular.ttf")) : null;
}
