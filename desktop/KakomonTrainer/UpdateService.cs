using System.IO;
using System.Net.Http;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace KakomonTrainer;

public record AvailableUpdate(string Version, string DownloadUrl, string? Digest);
public sealed class UpdateService : IDisposable
{
    private readonly HttpClient http = new() { Timeout = TimeSpan.FromMinutes(10) };
    public async Task<AvailableUpdate?> CheckAsync(string repository, CancellationToken token)
    {
        if (!Regex.IsMatch(repository, @"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")) throw new InvalidOperationException("設定に更新用GitHubリポジトリ（owner/repository）を指定してください。");
        http.DefaultRequestHeaders.UserAgent.ParseAdd("KakomonTrainer/2.0");
        var json = JsonNode.Parse(await http.GetStringAsync($"https://api.github.com/repos/{repository}/releases/latest", token))!;
        var tag = json["tag_name"]!.ToString().TrimStart('v');
        if (!Version.TryParse(tag, out var latest)) throw new InvalidDataException("更新のバージョン表記が不正です。");
        if (latest <= Assembly.GetExecutingAssembly().GetName().Version) return null;
        var asset = json["assets"]!.AsArray().FirstOrDefault(a => a?["name"]?.ToString() == "KakomonTrainer-Setup.exe") ?? throw new InvalidDataException("インストーラーがリリースにありません。");
        var url = asset["browser_download_url"]!.ToString();
        if (!url.StartsWith($"https://github.com/{repository}/releases/download/", StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("更新ファイルの配布先が不正です。");
        return new AvailableUpdate(tag, url, asset["digest"]?.ToString());
    }
    public async Task<string> DownloadAsync(AvailableUpdate update, CancellationToken token)
    {
        var dir = Path.Combine(AppSettings.ConfigRoot, "updates", update.Version); Directory.CreateDirectory(dir);
        var target = Path.Combine(dir, "KakomonTrainer-Setup.exe"); var partial = target + ".partial";
        try
        {
            using var response = await http.GetAsync(update.DownloadUrl, HttpCompletionOption.ResponseHeadersRead, token); response.EnsureSuccessStatusCode();
            await using (var stream = File.Create(partial)) await response.Content.CopyToAsync(stream, token);
            if (update.Digest?.StartsWith("sha256:") == true)
            {
                await using var input = File.OpenRead(partial); var hash = Convert.ToHexString(await SHA256.HashDataAsync(input, token));
                if (!hash.Equals(update.Digest[7..], StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("更新ファイルのハッシュが一致しません。");
            }
            File.Move(partial, target, true); return target;
        }
        finally { if (File.Exists(partial)) File.Delete(partial); }
    }
    public void Dispose() => http.Dispose();
}
