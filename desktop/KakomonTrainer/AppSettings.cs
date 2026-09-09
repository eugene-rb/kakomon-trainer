using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace KakomonTrainer;

public sealed class AppSettings
{
    public static string ConfigRoot => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "KakomonTrainer");
    public string DataRoot { get; set; } = Path.Combine(ConfigRoot, "data");
    public Dictionary<string, string> Values { get; set; } = new(StringComparer.OrdinalIgnoreCase);
    public string Get(string key, string fallback = "") => Values.TryGetValue(key, out var value) && !string.IsNullOrWhiteSpace(value) ? value : fallback;
    public string Provider => Get("GRADING_PROVIDER", "anthropic");
    public string Prefix => Provider switch { "openai_compatible" => "LLM", _ => Provider.ToUpperInvariant() };
    public string Model => Get(Prefix + "_MODEL", Get("GRADING_MODEL"));
    public string ApiKey => Get(Prefix + "_API_KEY");
    public static AppSettings Load()
    {
        Directory.CreateDirectory(ConfigRoot);
        var settings = new AppSettings();
        // Locate a development checkout without relying on the launching working directory.
        var root = new DirectoryInfo(AppContext.BaseDirectory);
        while (root != null && !File.Exists(Path.Combine(root.FullName, "app", "models.py"))) root = root.Parent;
        var legacyRoot = root?.FullName ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "KakomonTrainer");
        if (root != null || File.Exists(Path.Combine(legacyRoot, ".env")))
        {
            settings.DataRoot = Path.Combine(legacyRoot, "data");
            var env = Path.Combine(legacyRoot, ".env");
            if (File.Exists(env)) foreach (var line in File.ReadLines(env))
            {
                var text = line.Trim(); var at = text.IndexOf('=');
                if (at < 1 || text.StartsWith('#')) continue;
                var key = text[..at].Trim(); var value = text[(at + 1)..].Trim();
                if (value.StartsWith('"') || value.StartsWith('\''))
                { var closing = value.IndexOf(value[0], 1); if (closing > 0) value = value[1..closing]; }
                else { var comment = value.IndexOf(" #", StringComparison.Ordinal); if (comment >= 0) value = value[..comment].TrimEnd(); }
                if ((key is "GOOGLE_APPLICATION_CREDENTIALS" or "DATA_ROOT") && value.Length > 0 && !Path.IsPathRooted(value)) value = Path.GetFullPath(Path.Combine(legacyRoot, value));
                settings.Values[key] = value;
            }
            settings.DataRoot = settings.Get("DATA_ROOT", settings.DataRoot);
        }
        var path = Path.Combine(ConfigRoot, "desktop-settings.bin");
        if (File.Exists(path))
        {
            var saved = JsonSerializer.Deserialize<AppSettings>(ProtectedData.Unprotect(File.ReadAllBytes(path), null, DataProtectionScope.CurrentUser))!;
            settings.DataRoot = saved.DataRoot;
            foreach (var pair in saved.Values) settings.Values[pair.Key] = pair.Value;
        }
        foreach (System.Collections.DictionaryEntry pair in Environment.GetEnvironmentVariables())
            if (pair.Key is string key && (settings.Values.ContainsKey(key) || key.Contains("API_KEY") || key.EndsWith("_MODEL"))) settings.Values[key] = pair.Value?.ToString() ?? "";
        return settings;
    }
    public void Save()
    {
        Directory.CreateDirectory(ConfigRoot);
        var path = Path.Combine(ConfigRoot, "desktop-settings.bin");
        var bytes = ProtectedData.Protect(JsonSerializer.SerializeToUtf8Bytes(this), null, DataProtectionScope.CurrentUser);
        File.WriteAllBytes(path + ".tmp", bytes); File.Move(path + ".tmp", path, true);
    }
}
