using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Imaging;

namespace KakomonTrainer;

/// <summary>ファイル名から過去問メタデータを判定できなかったときの手動入力。</summary>
public sealed class MetadataDialog : Window
{
    private static readonly string[] TrackChoices = ["指定なし", "文系", "理系", "共通"];
    private readonly TextBox university = new();
    private readonly TextBox year = new();
    private readonly ComboBox track = new() { ItemsSource = TrackChoices };
    private readonly TextBox subject = new();

    public ExamMeta? Result { get; private set; }

    public MetadataDialog(string title, ExamMeta prefill, IEnumerable<string> unresolved)
    {
        Title = "過去問メタデータの確認";
        Width = 460;
        SizeToContent = SizeToContent.Height;
        ResizeMode = ResizeMode.NoResize;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        Icon = BitmapFrame.Create(new Uri("pack://application:,,,/KakomonTrainer;component/Assets/app.ico"));

        university.Text = prefill.University;
        year.Text = prefill.Year > 0 ? prefill.Year.ToString() : "";
        track.SelectedItem = prefill.Track.Length == 0 ? "指定なし" : prefill.Track;
        subject.Text = prefill.Subject;

        var missing = string.Join("・", unresolved);
        var panel = new StackPanel { Margin = new Thickness(20) };
        panel.Children.Add(new TextBlock
        {
            Text = $"「{title}」のファイル名から{(missing.Length > 0 ? missing + "を" : "一部を")}判定できませんでした。ネットから配点を調べるのに使うため、内容を確認してください。",
            TextWrapping = TextWrapping.Wrap,
            Margin = new Thickness(0, 0, 0, 14),
        });
        void Row(string label, Control input)
        {
            panel.Children.Add(new TextBlock { Text = label, Margin = new Thickness(0, 6, 0, 2) });
            input.MinWidth = 400;
            System.Windows.Automation.AutomationProperties.SetName(input, label);
            panel.Children.Add(input);
        }
        Row("大学", university);
        Row("年度（西暦）", year);
        Row("文理", track);
        Row("科目", subject);

        var ok = new Button { Content = "OK", MinWidth = 92, IsDefault = true, Margin = new Thickness(0, 0, 8, 0) };
        var cancel = new Button { Content = "キャンセル", MinWidth = 92, IsCancel = true };
        ok.Click += (_, _) =>
        {
            if (university.Text.Trim().Length == 0 || subject.Text.Trim().Length == 0
                || !int.TryParse(year.Text.Trim(), out var parsed) || parsed is < 1990 or > 2100)
            {
                MessageBox.Show(this, "大学・科目を入力し、年度は1990〜2100の西暦で入力してください。", "入力を確認してください", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }
            var selected = track.SelectedItem as string ?? "指定なし";
            Result = new ExamMeta(university.Text.Trim(), parsed, selected == "指定なし" ? "" : selected, subject.Text.Trim());
            DialogResult = true;
        };
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 18, 0, 0) };
        buttons.Children.Add(ok);
        buttons.Children.Add(cancel);
        panel.Children.Add(buttons);
        Content = panel;
    }
}
