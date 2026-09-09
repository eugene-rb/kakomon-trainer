using System.IO;
using System.Text.Json;

namespace KakomonTrainer;

public sealed class ExplanationEntry
{
    public string Key { get; set; } = "";
    public string ExamLabel { get; set; } = "";
    public List<ExplanationNote> Notes { get; set; } = [];
    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.Now;
}
public sealed class ExplanationNote
{
    public string QuestionId { get; set; } = "";
    public string Notes { get; set; } = "";
    public List<string> Sources { get; set; } = [];
}

/// <summary>
/// 解答解説の補助資料。ローカルの模範解答・採点基準が最優先で、それが無い設問だけ、
/// ネット上の情報を要約したメモを採点の参考資料として使う。年度ごとに保存する。
/// 保存先は <c>%LOCALAPPDATA%/KakomonTrainer/exam-explanations.json</c>。
/// </summary>
public sealed class ExplanationCache
{
    private readonly string path;
    private List<ExplanationEntry> items;

    public ExplanationCache(string? path = null)
    {
        this.path = path ?? Path.Combine(AppSettings.ConfigRoot, "exam-explanations.json");
        items = Load();
    }

    private List<ExplanationEntry> Load()
    {
        try { return File.Exists(path) ? JsonSerializer.Deserialize<List<ExplanationEntry>>(File.ReadAllText(path), DataStore.Json) ?? [] : []; }
        catch { return []; }
    }

    private static string KeyFor(ExamMeta meta) => $"{meta.University}|{meta.Track}|{meta.Subject}|{meta.Year}";

    public int Count => items.Sum(e => e.Notes.Count);

    public string? Lookup(ExamMeta meta, string questionId)
    {
        var entry = items.FirstOrDefault(e => e.Key == KeyFor(meta));
        var note = entry?.Notes.FirstOrDefault(n => string.Equals(n.QuestionId, questionId, StringComparison.OrdinalIgnoreCase));
        return string.IsNullOrWhiteSpace(note?.Notes) ? null : note!.Notes;
    }

    public void Store(ExamMeta meta, IEnumerable<ExplanationNote> notes)
    {
        var fresh = notes.Where(n => !string.IsNullOrWhiteSpace(n.Notes) && n.QuestionId.Length > 0).ToList();
        if (fresh.Count == 0) return;
        var entry = items.FirstOrDefault(e => e.Key == KeyFor(meta));
        if (entry == null) { entry = new ExplanationEntry { Key = KeyFor(meta) }; items.Add(entry); }
        entry.ExamLabel = meta.Describe();
        foreach (var note in fresh)
        {
            entry.Notes.RemoveAll(n => string.Equals(n.QuestionId, note.QuestionId, StringComparison.OrdinalIgnoreCase));
            entry.Notes.Add(note);
        }
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
