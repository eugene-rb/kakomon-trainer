using System.ComponentModel;
using System.IO;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Shapes;
using Microsoft.Win32;

namespace KakomonTrainer;

public partial class MainWindow : Window
{
    private readonly AppSettings settings;
    private DataStore store;
    private readonly PdfService pdf = new();
    private AiService ai;
    private Template? editing;
    private Template? sessionTemplate;
    private Session? session;
    private GradeResult? result;
    private List<byte[]> editorPages = [];
    private int pageIndex;
    private string savedTemplate = "", savedTranscriptions = "", savedScores = "";
    private CancellationTokenSource? operation;
    private Point? dragStart;
    private Rectangle? dragRect;
    private int previousTab;
    private bool changingTab;
    private readonly Dictionary<string, Control> settingControls = [];

    public MainWindow(AppSettings settings, DataStore store)
    {
        this.settings = settings; this.store = store; ai = new AiService(settings, store, pdf);
        InitializeComponent(); FormatBox.ItemsSource = Formats.All;
        BuildSettings(); RefreshLibrary();
        Closed += (_, _) => { ai.Dispose(); operation?.Dispose(); };
    }
    private static string Snapshot<T>(T value) => JsonSerializer.Serialize(value, DataStore.Json);
    private void RefreshLibrary()
    {
        TemplateGrid.ItemsSource = store.Templates(); SessionGrid.ItemsSource = store.Sessions();
        // A previous process cannot still be doing this work; make interrupted work explicit.
        foreach (var s in ((List<Session>)SessionGrid.ItemsSource).Where(s => s.Status is "processing" or "grading"))
        {
            s.Status = s.Status == "grading" ? "ready" : "failed"; s.Error = "前回の処理が中断されました。再実行してください。"; store.SaveSession(s);
        }
        StatusText.Text = store.LoadWarnings.Count > 0 ? string.Join("\n", store.LoadWarnings) : $"保存先: {store.Root}";
    }
    private async Task Run(Func<CancellationToken, Task> action)
    {
        if (operation != null) return;
        operation = new CancellationTokenSource(); Tabs.IsEnabled = false; BusyBar.Visibility = CancelButton.Visibility = Visibility.Visible;
        try { await action(operation.Token); }
        catch (OperationCanceledException) { StatusText.Text = "処理を中止しました。"; }
        catch (Exception ex) { StatusText.Text = ex.Message; MessageBox.Show(this, ex.Message, "処理に失敗しました", MessageBoxButton.OK, MessageBoxImage.Error); }
        finally { operation.Dispose(); operation = null; Tabs.IsEnabled = true; BusyBar.Visibility = CancelButton.Visibility = Visibility.Collapsed; }
    }
    private IProgress<string> Progress() => new Progress<string>(text => StatusText.Text = text);
    private static string[]? PickFiles(string filter, bool multiple = false)
    {
        var dialog = new OpenFileDialog { Filter = filter, Multiselect = multiple };
        return dialog.ShowDialog() == true ? dialog.FileNames : null;
    }
    private Template SelectedTemplate() => TemplateGrid.SelectedItem as Template ?? throw new InvalidOperationException("用紙を選択してください。");
    private async void Register_Click(object sender, RoutedEventArgs e)
    {
        var files = PickFiles("用紙・参照資料|*.pdf;*.md;*.txt", true); if (files == null) return;
        await Run(async token =>
        {
            var groups = Registration.Plan(files);
            var summary = string.Join("\n\n", groups.Select(g => $"{System.IO.Path.GetFileName(g.Sheet)}\n  解説・基準: {(g.References.Count == 0 ? "なし" : string.Join(", ", g.References.Select(System.IO.Path.GetFileName)))}"));
            if (MessageBox.Show(this, summary + "\n\nこの対応で登録しますか？", "用紙と資料の対応確認", MessageBoxButton.OKCancel) != MessageBoxResult.OK) return;
            var failures = new List<string>(); var count = 0;
            foreach (var group in groups)
            {
                token.ThrowIfCancellationRequested(); StatusText.Text = $"{group.Title} を登録中…";
                try
                {
                    var t = await pdf.RegisterAsync(store, group.Sheet, group.Title, token);
                    foreach (var reference in group.References)
                    {
                        var relative = "refs/" + System.IO.Path.GetFileName(reference); File.Copy(reference, DataStore.Child(store.TemplateDir(t.TemplateId), relative)); t.DefaultRefs.Add(relative);
                    }
                    store.SaveTemplate(t); count++;
                }
                catch (Exception ex) when (ex is not OperationCanceledException) { failures.Add($"{group.Title}: {ex.Message}"); }
            }
            RefreshLibrary(); StatusText.Text = $"{count}組を登録しました。" + string.Join("\n", failures);
            if (failures.Count > 0) MessageBox.Show(this, string.Join("\n", failures), "登録できなかった用紙");
        });
    }
    private async void Edit_Click(object sender, RoutedEventArgs e)
    {
        if (!ConfirmEdits()) return;
        await Run(async token =>
        {
            var t = store.LoadTemplate(SelectedTemplate().TemplateId);
            var pages = await pdf.RenderAsync(DataStore.Child(store.TemplateDir(t.TemplateId), t.BlankPdf), 120, token);
            editing = t; editorPages = pages; pageIndex = 0; TitleBox.Text = t.Title; QuestionList.ItemsSource = t.Questions;
            savedTemplate = TemplateSnapshot(); ShowEditorPage(); GoTab(1); QuestionList.SelectedIndex = t.Questions.Count > 0 ? 0 : -1;
        });
    }
    private string TemplateSnapshot() { if (editing == null) return ""; editing.Title = TitleBox.Text; return Snapshot(editing); }
    private bool HasValidationErrors(DependencyObject parent)
    {
        if (Validation.GetHasError(parent)) return true;
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(parent); i++) if (HasValidationErrors(VisualTreeHelper.GetChild(parent, i))) return true;
        return false;
    }
    private void SaveTemplate()
    {
        if (editing == null) return;
        if (HasValidationErrors(QuestionForm)) throw new InvalidDataException("配点と許容誤差の入力を確認してください。");
        editing.Title = TitleBox.Text;
        if (string.IsNullOrWhiteSpace(editing.Title)) throw new InvalidDataException("用紙名を入力してください。");
        store.SaveTemplate(editing); savedTemplate = TemplateSnapshot(); StatusText.Text = "用紙を保存しました。"; RefreshLibrary();
    }
    private void SaveTemplate_Click(object sender, RoutedEventArgs e) => SaveTemplate();
    private void AddQuestion_Click(object sender, RoutedEventArgs e)
    {
        if (editing == null) { StatusText.Text = "先に用紙を開いてください。"; return; }
        var n = 1; while (editing.Questions.Any(q => q.Id == $"Q{n}")) n++;
        var q = new Question { Id = $"Q{n}", Refs = [.. editing.DefaultRefs] }; editing.Questions.Add(q); QuestionList.SelectedItem = q;
    }
    private void DeleteQuestion_Click(object sender, RoutedEventArgs e)
    {
        if (editing != null && QuestionList.SelectedItem is Question q) { editing.Questions.Remove(q); DrawRegions(); }
    }
    private void Question_Changed(object sender, SelectionChangedEventArgs e) { QuestionForm.DataContext = QuestionList.SelectedItem; DrawRegions(); }
    private void Format_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (QuestionList.SelectedItem is not Question q) return;
        var format = FormatBox.SelectedValue?.ToString(); var language = format switch { "translation_ja" => "ja", "translation_en" or "composition_en" => "en", _ => null };
        LanguageBox.IsEnabled = language == null;
        if (language != null) { q.Language = language; LanguageBox.SelectedValue = language; }
    }
    private void ShowEditorPage()
    {
        if (editorPages.Count == 0) return;
        var image = PdfService.Bitmap(editorPages[pageIndex]); SheetImage.Source = image;
        var width = 730d; var height = width * image.PixelHeight / image.PixelWidth;
        SheetImage.Width = RegionCanvas.Width = width; SheetImage.Height = RegionCanvas.Height = height;
        PageLabel.Text = $"{pageIndex + 1} / {editorPages.Count} ページ"; DrawRegions();
    }
    private void PreviousPage_Click(object sender, RoutedEventArgs e) { if (pageIndex > 0) { pageIndex--; ShowEditorPage(); } }
    private void NextPage_Click(object sender, RoutedEventArgs e) { if (pageIndex + 1 < editorPages.Count) { pageIndex++; ShowEditorPage(); } }
    private void DrawRegions()
    {
        if (RegionCanvas == null) return; RegionCanvas.Children.Clear();
        if (editing == null) return;
        foreach (var q in editing.Questions) foreach (var r in q.Regions.Where(r => r.PageNo == pageIndex + 1))
        {
            var color = q == QuestionList.SelectedItem ? Colors.DodgerBlue : Colors.DarkOrange;
            var rect = new Rectangle { Width = (r.Rect[2] - r.Rect[0]) * RegionCanvas.Width, Height = (r.Rect[3] - r.Rect[1]) * RegionCanvas.Height, Stroke = new SolidColorBrush(color), StrokeThickness = 2, Fill = new SolidColorBrush(Color.FromArgb(25, color.R, color.G, color.B)), IsHitTestVisible = false };
            Canvas.SetLeft(rect, r.Rect[0] * RegionCanvas.Width); Canvas.SetTop(rect, r.Rect[1] * RegionCanvas.Height); RegionCanvas.Children.Add(rect);
            var label = new TextBlock { Text = q.Id, Background = Brushes.White, Foreground = new SolidColorBrush(color), IsHitTestVisible = false };
            Canvas.SetLeft(label, r.Rect[0] * RegionCanvas.Width); Canvas.SetTop(label, Math.Max(0, r.Rect[1] * RegionCanvas.Height - 20)); RegionCanvas.Children.Add(label);
        }
    }
    private Point Clamp(Point p) => new(Math.Clamp(p.X, 0, RegionCanvas.Width), Math.Clamp(p.Y, 0, RegionCanvas.Height));
    private void Region_Down(object sender, MouseButtonEventArgs e)
    {
        if (QuestionList.SelectedItem is not Question || editing == null) return;
        dragStart = Clamp(e.GetPosition(RegionCanvas)); dragRect = new Rectangle { Stroke = Brushes.DodgerBlue, StrokeThickness = 2, Fill = new SolidColorBrush(Color.FromArgb(35, 37, 99, 235)), IsHitTestVisible = false };
        RegionCanvas.Children.Add(dragRect); RegionCanvas.CaptureMouse();
    }
    private void Region_Move(object sender, MouseEventArgs e)
    {
        if (dragStart is not Point start || dragRect == null) return; var p = Clamp(e.GetPosition(RegionCanvas));
        Canvas.SetLeft(dragRect, Math.Min(start.X, p.X)); Canvas.SetTop(dragRect, Math.Min(start.Y, p.Y)); dragRect.Width = Math.Abs(p.X - start.X); dragRect.Height = Math.Abs(p.Y - start.Y);
    }
    private void Region_Up(object sender, MouseButtonEventArgs e)
    {
        if (dragStart is Point start && QuestionList.SelectedItem is Question q)
        {
            var p = Clamp(e.GetPosition(RegionCanvas));
            if (Math.Abs(p.X - start.X) >= 5 && Math.Abs(p.Y - start.Y) >= 5) q.Regions.Add(new Region { PageNo = pageIndex + 1, Rect = [Math.Min(p.X, start.X) / RegionCanvas.Width, Math.Min(p.Y, start.Y) / RegionCanvas.Height, Math.Max(p.X, start.X) / RegionCanvas.Width, Math.Max(p.Y, start.Y) / RegionCanvas.Height] });
        }
        dragStart = null; dragRect = null; RegionCanvas.ReleaseMouseCapture(); DrawRegions();
    }
    private void DeleteRegion_Click(object sender, RoutedEventArgs e) { if (QuestionList.SelectedItem is Question q && RegionList.SelectedItem is Region r) { q.Regions.Remove(r); DrawRegions(); } }
    private void AddReference_Click(object sender, RoutedEventArgs e)
    {
        if (editing == null || QuestionList.SelectedItem is not Question q) return;
        var files = PickFiles("参照資料|*.pdf;*.md;*.txt", true); if (files == null) return;
        foreach (var file in files)
        {
            var name = System.IO.Path.GetFileName(file); var relative = "refs/" + name; var target = DataStore.Child(store.TemplateDir(editing.TemplateId), relative);
            if (File.Exists(target)) { relative = "refs/" + Guid.NewGuid().ToString("N")[..6] + "-" + name; target = DataStore.Child(store.TemplateDir(editing.TemplateId), relative); }
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(target)!); File.Copy(file, target); q.Refs.Add(relative);
        }
        QuestionForm.DataContext = null; QuestionForm.DataContext = q;
    }
    private async void Blank_Click(object sender, RoutedEventArgs e) => await Run(async token => { var t = SelectedTemplate(); await ShowPdf(DataStore.Child(store.TemplateDir(t.TemplateId), t.BlankPdf), t.Title, token); });
    private async Task ShowPdf(string path, string title, CancellationToken token)
    {
        var images = await pdf.RenderAsync(path, 130, token); new PdfWindow(title, images) { Owner = this }.Show();
    }
    private void DeleteTemplate_Click(object sender, RoutedEventArgs e)
    {
        var t = SelectedTemplate();
        if (store.Sessions().Any(s => s.TemplateId == t.TemplateId)) throw new InvalidOperationException("答案から参照されている用紙は削除できません。");
        if (MessageBox.Show(this, $"「{t.Title}」を削除しますか？", "用紙の削除", MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes) return;
        Directory.Delete(store.TemplateDir(t.TemplateId), true);
        if (editing?.TemplateId == t.TemplateId) { editing = null; QuestionList.ItemsSource = null; SheetImage.Source = null; editorPages.Clear(); DrawRegions(); }
        RefreshLibrary();
    }
    private async void Scan_Click(object sender, RoutedEventArgs e)
    {
        if (!ConfirmEdits()) return;
        var files = PickFiles("スキャンした答案|*.pdf;*.png;*.jpg;*.jpeg;*.bmp;*.tif;*.tiff", true); if (files == null) return;
        var useOcr = OcrCheck.IsChecked == true;
        await Run(async token =>
        {
            sessionTemplate = store.LoadTemplate(SelectedTemplate().TemplateId);
            session = await new ScanService(store, pdf, ai).ScanAsync(sessionTemplate, files, useOcr, Progress(), token);
            OpenReview(); RefreshLibrary(); GoTab(2); StatusText.Text = session.Warnings.Count > 0 ? string.Join("\n", session.Warnings) : "画像と転記を確認し、保存して採点してください。";
        });
    }
    private async void OpenSession_Click(object sender, RoutedEventArgs e)
    {
        if (!ConfirmEdits()) return;
        await Run(_ =>
        {
            session = SessionGrid.SelectedItem as Session ?? throw new InvalidOperationException("答案を選択してください。");
            if (session.Status == "failed") throw new InvalidOperationException(session.Error ?? "取り込みに失敗した答案です。再取り込みしてください。");
            var snapshot = System.IO.Path.Combine(store.SessionDir(session.SessionId), "template-snapshot.json");
            sessionTemplate = File.Exists(snapshot) ? DataStore.Read<Template>(snapshot) : store.LoadTemplate(session.TemplateId); OpenReview(); GoTab(session.Status == "graded" ? 3 : 2); return Task.CompletedTask;
        });
    }
    private void OpenReview()
    {
        if (session == null) return;
        ReviewTitle.Text = $"{sessionTemplate?.Title} — 転記確認"; TranscriptionList.ItemsSource = session.Transcriptions; TranscriptionList.SelectedIndex = 0;
        savedTranscriptions = Snapshot(session.Transcriptions); result = store.Result(session); ShowResult();
    }
    private void Transcription_Changed(object sender, SelectionChangedEventArgs e)
    {
        TranscriptionForm.DataContext = TranscriptionList.SelectedItem; CropImages.Children.Clear();
        if (session == null || TranscriptionList.SelectedItem is not Transcription t) return;
        foreach (var crop in session.Crops.Where(c => c.QuestionId == t.QuestionId).OrderBy(c => c.Index))
        {
            var path = DataStore.Child(System.IO.Path.Combine(store.SessionDir(session.SessionId), "crops"), crop.Filename);
            if (File.Exists(path)) CropImages.Children.Add(new Image { Source = PdfService.Bitmap(File.ReadAllBytes(path)), Stretch = Stretch.Uniform, Margin = new Thickness(0, 0, 0, 15) });
            if (crop.OcrError != null)
            {
                var error = new TextBlock { Text = "OCRエラー: " + crop.OcrError, TextWrapping = TextWrapping.Wrap };
                error.SetResourceReference(TextBlock.ForegroundProperty, "SystemFillColorCriticalBrush"); CropImages.Children.Add(error);
            }
        }
    }
    private void SaveTranscriptions()
    {
        if (session == null) return;
        if (Snapshot(session.Transcriptions) == savedTranscriptions) return;
        var previous = JsonSerializer.Deserialize<List<Transcription>>(savedTranscriptions, DataStore.Json)!;
        foreach (var t in session.Transcriptions) if (previous.SingleOrDefault(p => p.QuestionId == t.QuestionId)?.TranscriptionText != t.TranscriptionText) t.TranscriptionEdited = true;
        store.SaveTranscriptions(session); savedTranscriptions = Snapshot(session.Transcriptions); result = null; ShowResult(); StatusText.Text = "転記を保存しました。採点結果は再採点後に表示します。";
    }
    private void SaveTranscriptions_Click(object sender, RoutedEventArgs e) => SaveTranscriptions();
    private async void Grade_Click(object sender, RoutedEventArgs e)
    {
        if (session == null || sessionTemplate == null) return;
        SaveTranscriptions();
        await Run(async token =>
        {
            result = await new GradingService(store, pdf, ai, settings).GradeAsync(sessionTemplate, session, Progress(), token);
            ShowResult(); RefreshLibrary(); GoTab(3); StatusText.Text = "採点完了。" + string.Join(" / ", result.Warnings);
        });
    }
    private void ShowResult()
    {
        ResultGrid.ItemsSource = result?.Questions; savedScores = result == null ? "" : Snapshot(result);
        ResultTitle.Text = result == null ? "採点結果はありません" : $"{result.TotalScore} / {result.TotalMaxScore} 点" + (result.Questions.Any(q => q.Score == null) ? "（未確定の設問あり）" : "");
        ResultDetails.Text = result == null ? "転記を確認してから採点してください。" : PdfService.ReviewText(result);
    }
    private async void SaveScores_Click(object sender, RoutedEventArgs e)
    {
        if (result == null || session == null || sessionTemplate == null) return;
        ResultGrid.CommitEdit(DataGridEditingUnit.Cell, true); ResultGrid.CommitEdit(DataGridEditingUnit.Row, true);
        if (HasValidationErrors(ResultGrid) || result.Questions.Any(q => q.Score < 0 || q.Score > q.MaxScore)) throw new InvalidDataException("得点は0から配点までの整数にしてください。");
        await Run(async token =>
        {
            var old = JsonSerializer.Deserialize<GradeResult>(savedScores, DataStore.Json)!;
            foreach (var q in result.Questions.Where(q => old.Questions.Single(p => p.Id == q.Id).Score != q.Score)) { q.Confidence = "high"; q.Model = "manual"; q.Feedback += "\n点数を手動で確認・修正しました。"; }
            await new GradingService(store, pdf, ai, settings).SaveResult(sessionTemplate, session, result, token); ShowResult(); StatusText.Text = "修正した点数とPDF・復習ログを保存しました。";
        });
    }
    private string ResultPdfPath()
    {
        if (session == null || session.Status != "graded" || result == null) throw new InvalidOperationException("採点済みの答案を開いてください。");
        if (Snapshot(result) != savedScores) throw new InvalidOperationException("点数の修正を保存してからPDFを開いてください。");
        return System.IO.Path.Combine(store.ResultDir(session.SessionId), "graded.pdf");
    }
    private async void GradedPdf_Click(object sender, RoutedEventArgs e) => await Run(async token => await ShowPdf(ResultPdfPath(), "赤入れPDF", token));
    private void ExportPdf_Click(object sender, RoutedEventArgs e)
    {
        var path = ResultPdfPath(); var dialog = new SaveFileDialog { Filter = "PDF|*.pdf", FileName = session!.SessionId + "-graded.pdf" };
        if (dialog.ShowDialog(this) == true && !string.Equals(path, dialog.FileName, StringComparison.OrdinalIgnoreCase)) File.Copy(path, dialog.FileName, true);
    }
    private void ExportLog_Click(object sender, RoutedEventArgs e)
    {
        if (result == null) return; ResultPdfPath();
        var dialog = new SaveFileDialog { Filter = "Markdown|*.md", FileName = session!.SessionId + "-review.md" };
        if (dialog.ShowDialog(this) == true) File.WriteAllText(dialog.FileName, PdfService.ReviewText(result));
    }
    private void Stats_Click(object sender, RoutedEventArgs e)
    {
        var results = store.Sessions().Select(store.Result).OfType<GradeResult>().ToList(); var questions = results.SelectMany(r => r.Questions).Where(q => q.Score != null).ToList();
        var totalMax = questions.Sum(q => q.MaxScore); var rate = totalMax == 0 ? 0 : (double)questions.Sum(q => q.Score ?? 0) / totalMax;
        StatsText.Text = $"採点済み答案  {results.Count} 件\n確定した設問  {questions.Count} 問\n得点率  {rate:P1}\n\n復習したい項目\n\n" + string.Join("\n", questions.SelectMany(q => q.Issues).GroupBy(i => i.Tag).OrderByDescending(g => g.Sum(i => i.Deduction)).Select(g => $"{g.Key}    {g.Count()}件 / 合計 {g.Sum(i => i.Deduction)}点の減点"));
        var templates = store.Templates().ToDictionary(t => t.TemplateId);
        var categories = results.SelectMany(r => r.Questions.Where(q => q.Score != null).Select(q => (Question: q, Type: templates.TryGetValue(r.TemplateId, out var template) ? template.Questions.FirstOrDefault(item => item.Id == q.Id)?.Type ?? "" : ""))).GroupBy(item => string.IsNullOrWhiteSpace(item.Type) ? "未分類" : item.Type);
        StatsText.Text += "\n\n分類別の得点率\n\n" + string.Join("\n", categories.Select(g => $"{g.Key}    {(g.Sum(item => item.Question.MaxScore) == 0 ? 0 : (double)g.Sum(item => item.Question.Score ?? 0) / g.Sum(item => item.Question.MaxScore)):P1}"));
    }
    private void Refresh_Click(object sender, RoutedEventArgs e) => RefreshLibrary();
    private void Cancel_Click(object sender, RoutedEventArgs e) => operation?.Cancel();
    private bool ConfirmEdits()
    {
        ResultGrid.CommitEdit(DataGridEditingUnit.Cell, true); ResultGrid.CommitEdit(DataGridEditingUnit.Row, true);
        if (editing != null && (TemplateSnapshot() != savedTemplate || HasValidationErrors(QuestionForm)))
        {
            var choice = MessageBox.Show(this, "用紙の変更を保存しますか？", "未保存の編集", MessageBoxButton.YesNoCancel);
            if (choice == MessageBoxResult.Cancel) return false;
            if (choice == MessageBoxResult.Yes) SaveTemplate();
            else { editing = store.LoadTemplate(editing.TemplateId); TitleBox.Text = editing.Title; QuestionList.ItemsSource = editing.Questions; savedTemplate = TemplateSnapshot(); DrawRegions(); }
        }
        if (session != null && Snapshot(session.Transcriptions) != savedTranscriptions)
        {
            var choice = MessageBox.Show(this, "転記の変更を保存しますか？", "未保存の転記", MessageBoxButton.YesNoCancel);
            if (choice == MessageBoxResult.Cancel) return false;
            if (choice == MessageBoxResult.Yes) SaveTranscriptions();
            else { session.Transcriptions = JsonSerializer.Deserialize<List<Transcription>>(savedTranscriptions, DataStore.Json)!; TranscriptionList.ItemsSource = session.Transcriptions; TranscriptionList.SelectedIndex = 0; }
        }
        if (result != null && (Snapshot(result) != savedScores || HasValidationErrors(ResultGrid)))
        {
            if (MessageBox.Show(this, "点数の修正が未保存です。修正を破棄して移動しますか？\n保存する場合は「いいえ」を選び、「点数の修正を保存」を押してください。", "未保存の点数", MessageBoxButton.YesNo) != MessageBoxResult.Yes) return false;
            result = JsonSerializer.Deserialize<GradeResult>(savedScores, DataStore.Json); ShowResult();
        }
        return true;
    }
    private void GoTab(int index) { changingTab = true; Tabs.SelectedIndex = previousTab = index; changingTab = false; }
    private void Tabs_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (e.Source != Tabs || changingTab || !IsLoaded) return;
        var target = Tabs.SelectedIndex;
        try { if (!ConfirmEdits()) { GoTab(previousTab); return; } previousTab = target; }
        catch { GoTab(previousTab); throw; }
    }
    private void Window_Closing(object? sender, CancelEventArgs e)
    {
        if (operation != null) { operation.Cancel(); StatusText.Text = "処理の中止を待ってから閉じてください。"; e.Cancel = true; return; }
        try { e.Cancel = !ConfirmEdits(); } catch (Exception ex) { MessageBox.Show(this, ex.Message); e.Cancel = true; }
    }
    private void BuildSettings()
    {
        void Field(string key, string label, string value, string[]? choices = null)
        {
            SettingFields.Children.Add(new TextBlock { Text = label });
            Control input;
            if (choices != null) input = new ComboBox { ItemsSource = choices, SelectedItem = value };
            else if (key.EndsWith("_KEY")) input = new PasswordBox { Password = value, Padding = new Thickness(8), Margin = new Thickness(0, 4, 0, 10) };
            else input = new TextBox { Text = value };
            System.Windows.Automation.AutomationProperties.SetName(input, label);
            System.Windows.Automation.AutomationProperties.SetAutomationId(input, key);
            if (key == "APPEARANCE_THEME" && input is ComboBox themePicker)
                themePicker.SelectionChanged += (_, _) => DesktopAppearance.Apply(themePicker.SelectedItem?.ToString() ?? "");
            input.MinWidth = 620; settingControls[key] = input; SettingFields.Children.Add(input);
        }
        Field("APPEARANCE_THEME", "アプリのテーマ", settings.Get("APPEARANCE_THEME", DesktopAppearance.Choices[0]), DesktopAppearance.Choices);
        Field("DATA_ROOT", "データ保存先（既存のdataフォルダーも指定可能）", settings.DataRoot);
        Field("OCR_BACKEND", "OCRサービス", settings.Get("OCR_BACKEND", "google_vision"), ["google_vision", "azure_di", "claude_vision"]);
        Field("GOOGLE_APPLICATION_CREDENTIALS", "GoogleサービスアカウントJSONの絶対パス", settings.Get("GOOGLE_APPLICATION_CREDENTIALS"));
        Field("AZURE_DI_ENDPOINT", "Azure Document Intelligence エンドポイント", settings.Get("AZURE_DI_ENDPOINT"));
        Field("AZURE_DI_KEY", "Azure APIキー", settings.Get("AZURE_DI_KEY"));
        Field("GRADING_PROVIDER", "採点サービス", settings.Provider, ["anthropic", "openai", "kimi", "openai_compatible"]);
        foreach (var prefix in new[] { "ANTHROPIC", "OPENAI", "KIMI", "LLM" })
        {
            Field(prefix + "_API_KEY", prefix + " APIキー", settings.Get(prefix + "_API_KEY"));
            Field(prefix + "_MODEL", prefix + " モデル名", settings.Get(prefix + "_MODEL"));
            if (prefix != "ANTHROPIC") Field(prefix + "_BASE_URL", prefix + " APIベースURL（/v1まで）", settings.Get(prefix + "_BASE_URL"));
        }
        Field("ANSWER_ONLY_LLM_FALLBACK", "答えのみの曖昧な表記をClaudeで確認", settings.Get("ANSWER_ONLY_LLM_FALLBACK", "true"), ["true", "false"]);
        Field("ANSWER_ONLY_MODEL", "正誤確認用Claudeモデル", settings.Get("ANSWER_ONLY_MODEL", "claude-haiku-4-5"));
        Field("DOUBLE_GRADING", "記述式を二重採点", settings.Get("DOUBLE_GRADING", "false"), ["true", "false"]);
        Field("DOUBLE_GRADING_THRESHOLD", "二重採点で要確認とする点差", settings.Get("DOUBLE_GRADING_THRESHOLD", "3"));
        Field("UPDATE_REPOSITORY", "更新用GitHubリポジトリ（owner/repository）", settings.Get("UPDATE_REPOSITORY"));
        var update = new Button { Content = "アプリの更新を確認", HorizontalAlignment = HorizontalAlignment.Left }; update.Click += Update_Click; SettingFields.Children.Add(update);
    }
    private async void Update_Click(object sender, RoutedEventArgs e)
    {
        if (!ConfirmEdits()) return;
        string? installer = null;
        await Run(async token =>
        {
            using var updates = new UpdateService(); var available = await updates.CheckAsync(settings.Get("UPDATE_REPOSITORY"), token);
            if (available == null) { StatusText.Text = "新しいバージョンはありません。"; return; }
            if (MessageBox.Show(this, $"バージョン {available.Version} をダウンロードしてインストールしますか？", "アプリの更新", MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
            StatusText.Text = "更新ファイルをダウンロード中…"; installer = await updates.DownloadAsync(available, token);
        });
        if (installer != null) { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(installer) { UseShellExecute = true }); Close(); }
    }
    private void SaveSettings_Click(object sender, RoutedEventArgs e)
    {
        if (!ConfirmEdits()) return;
        var values = settingControls.ToDictionary(p => p.Key, p => p.Value switch { TextBox text => text.Text.Trim(), PasswordBox password => password.Password, ComboBox combo => combo.SelectedItem?.ToString() ?? "", _ => "" });
        if (!System.IO.Path.IsPathFullyQualified(values["DATA_ROOT"])) throw new InvalidDataException("データ保存先は絶対パスで指定してください。");
        if (!int.TryParse(values["DOUBLE_GRADING_THRESHOLD"], out var threshold) || threshold < 1) throw new InvalidDataException("二重採点の点差は1以上の整数にしてください。");
        var replacement = new DataStore(values["DATA_ROOT"]);
        foreach (var pair in values) settings.Values[pair.Key] = pair.Value;
        settings.DataRoot = replacement.Root; settings.Save(); DesktopAppearance.Apply(settings.Get("APPEARANCE_THEME")); ai.Dispose(); store = replacement; ai = new AiService(settings, store, pdf);
        editing = null; session = null; sessionTemplate = null; result = null; editorPages.Clear(); SheetImage.Source = null; QuestionList.ItemsSource = null; TranscriptionList.ItemsSource = null; CropImages.Children.Clear(); DrawRegions(); ShowResult();
        RefreshLibrary(); StatusText.Text = "設定を保存しました。";
    }
}

public record RegistrationGroup(string Title, string Sheet, List<string> References);
public static class Registration
{
    public static List<RegistrationGroup> Plan(string[] files)
    {
        if (files.Length == 0) throw new InvalidDataException("用紙を選んでください。");
        if (files.Select(System.IO.Path.GetFileName).Distinct(StringComparer.OrdinalIgnoreCase).Count() != files.Length) throw new InvalidDataException("同名ファイルが重複しています。");
        var groups = files.Select(file =>
        {
            var stem = System.IO.Path.GetFileNameWithoutExtension(file); var match = Regex.Match(stem, @"^(.+)_(sheet|answers|rubric)$", RegexOptions.IgnoreCase);
            return (File: file, Title: match.Success ? match.Groups[1].Value : stem, Role: match.Success ? match.Groups[2].Value.ToLowerInvariant() : "sheet");
        }).GroupBy(f => f.Title, StringComparer.OrdinalIgnoreCase);
        return groups.Select(g =>
        {
            var sheets = g.Where(f => f.Role == "sheet").ToList(); var references = g.Where(f => f.Role != "sheet").Select(f => f.File).ToList();
            if (sheets.Count != 1 || !sheets[0].File.EndsWith(".pdf", StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException($"{g.Key}: 用紙を1枚組の *_sheet.pdf として選択してください。解説は同じ共通名の *_answers.pdf / *_rubric.md で対応付けます。");
            return new RegistrationGroup(g.Key, sheets[0].File, references);
        }).ToList();
    }
}
