using System.IO;
using System.Windows;

namespace KakomonTrainer;

public partial class App : Application
{
    private Mutex? instance;
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        instance = new Mutex(true, "Local\\KakomonTrainer.Wpf", out var first);
        if (!first) { MessageBox.Show("過去問トレーナーは起動済みです。"); Shutdown(); return; }
        DispatcherUnhandledException += (_, args) =>
        {
            MessageBox.Show(args.Exception.Message, "処理に失敗しました", MessageBoxButton.OK, MessageBoxImage.Error);
            args.Handled = true;
        };
        try
        {
            var settings = AppSettings.Load();
            DesktopAppearance.Apply(settings.Get("APPEARANCE_THEME"));
            if (e.Args is ["--data-root", var dataRoot]) settings.DataRoot = Path.GetFullPath(dataRoot);
            var store = new DataStore(settings.DataRoot);
            MainWindow = new MainWindow(settings, store);
            MainWindow.Show();
        }
        catch (Exception ex)
        {
            File.WriteAllText(Path.Combine(AppSettings.ConfigRoot, "startup-error.log"), ex.ToString());
            MessageBox.Show(ex.GetBaseException().Message, "起動できませんでした"); Shutdown(1);
        }
    }
    protected override void OnExit(ExitEventArgs e) { instance?.Dispose(); base.OnExit(e); }
}
