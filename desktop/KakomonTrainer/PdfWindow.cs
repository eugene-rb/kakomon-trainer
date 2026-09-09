using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Media;

namespace KakomonTrainer;

public sealed class PdfWindow : Window
{
    public PdfWindow(string title, List<byte[]> pages)
    {
        Title = title; Width = 960; Height = 850; WindowStartupLocation = WindowStartupLocation.CenterOwner;
        var document = new FixedDocument();
        foreach (var bytes in pages)
        {
            var bitmap = PdfService.Bitmap(bytes);
            var page = new FixedPage { Width = bitmap.PixelWidth * 96d / 130, Height = bitmap.PixelHeight * 96d / 130 };
            page.Children.Add(new Image { Source = bitmap, Width = page.Width, Height = page.Height, Stretch = Stretch.Fill });
            var content = new PageContent(); ((System.Windows.Markup.IAddChild)content).AddChild(page); document.Pages.Add(content);
        }
        Content = new DocumentViewer { Document = document };
    }
}
