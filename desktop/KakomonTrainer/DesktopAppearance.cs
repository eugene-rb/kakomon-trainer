using System.Windows;

namespace KakomonTrainer;

public static class DesktopAppearance
{
    public static readonly string[] Choices = ["Windowsに合わせる", "ライト", "ダーク"];
    public static void Apply(string choice)
    {
        // The .NET 9 Fluent theme API is experimental; keep usage isolated here.
#pragma warning disable WPF0001
        Application.Current.ThemeMode = choice switch
        {
            "ライト" => ThemeMode.Light,
            "ダーク" => ThemeMode.Dark,
            _ => ThemeMode.System
        };
#pragma warning restore WPF0001
    }
}
