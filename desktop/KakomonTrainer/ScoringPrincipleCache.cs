using System.IO;
using System.Text.Json;

namespace KakomonTrainer;

/// <summary>大学・科目・文理ごとに一般化した配点の骨子。年度には依存しない。</summary>
public sealed class ScoringPrinciple
{
    public string Key { get; set; } = "";
    public string University { get; set; } = "";
    public string Track { get; set; } = "";
    public string Subject { get; set; } = "";
    public string Principle { get; set; } = "";
    public int TypicalTotal { get; set; }
    public List<int> YearsObserved { get; set; } = [];
    public List<string> Sources { get; set; } = [];
    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.Now;
}

/// <summary>
/// 毎回ゼロから検索し直す負担を減らすため、確定した配点原則を保存しておく。
/// ウェブ検索は廃止せず、次回はこの原則を出発点として当該年度の問題用紙と最新情報で検証する。
/// 保存先は <c>%LOCALAPPDATA%/KakomonTrainer/scoring-principles.json</c>。
/// </summary>
public sealed class ScoringPrincipleCache
{
    private readonly string path;
    private List<ScoringPrinciple> items;

    public ScoringPrincipleCache(string? path = null)
    {
        this.path = path ?? Path.Combine(AppSettings.ConfigRoot, "scoring-principles.json");
        items = Load();
    }

    private List<ScoringPrinciple> Load()
    {
        // A corrupt or unreadable cache must not block the app; treat it as empty.
        try { return File.Exists(path) ? JsonSerializer.Deserialize<List<ScoringPrinciple>>(File.ReadAllText(path), DataStore.Json) ?? [] : []; }
        catch { return []; }
    }

    public int Count => items.Count;

    /// <summary>完全一致 → 文理を無視した一致 → 大学・科目のみの一致 の順で探す。</summary>
    public ScoringPrinciple? Lookup(ExamMeta meta)
        => items.FirstOrDefault(p => p.Key == meta.CacheKey)
        ?? items.FirstOrDefault(p => p.University == meta.University && p.Subject == meta.Subject
            && (p.Track == meta.Track || p.Track.Length == 0 || meta.Track.Length == 0))
        ?? items.FirstOrDefault(p => p.University == meta.University && p.Subject == meta.Subject);

    public void Update(ExamMeta meta, string principle, int typicalTotal, IEnumerable<string> sources)
    {
        if (string.IsNullOrWhiteSpace(principle)) return;
        var entry = items.FirstOrDefault(p => p.Key == meta.CacheKey);
        if (entry == null)
        {
            entry = new ScoringPrinciple { Key = meta.CacheKey, University = meta.University, Track = meta.Track, Subject = meta.Subject };
            items.Add(entry);
        }
        entry.Principle = principle.Trim();
        if (typicalTotal > 0) entry.TypicalTotal = typicalTotal;
        if (meta.Year > 0 && !entry.YearsObserved.Contains(meta.Year)) entry.YearsObserved.Add(meta.Year);
        entry.YearsObserved.Sort();
        entry.Sources = entry.Sources.Concat(sources)
            .Where(s => !string.IsNullOrWhiteSpace(s)).Select(s => s.Trim())
            .Distinct(StringComparer.OrdinalIgnoreCase).Take(20).ToList();
        entry.UpdatedAt = DateTimeOffset.Now;
        Save();
    }

    public void Clear()
    {
        items = [];
        Save();
    }

    private void Save()
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temp = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try { File.WriteAllText(temp, JsonSerializer.Serialize(items, DataStore.Json)); File.Move(temp, path, true); }
        finally { if (File.Exists(temp)) File.Delete(temp); }
    }
}
