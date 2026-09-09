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
    public QuestionRubric? Rubric { get; set; }
    public string QuestionId { get; set; } = "";
    public string Notes { get; set; } = "";
    public List<string> Sources { get; set; } = [];
}

public sealed class QuestionRubric
{
    public DateTimeOffset RetrievedAt { get; set; } = DateTimeOffset.UtcNow;
    public int SourceYear { get; set; }
    public string ExpectedAnswer { get; set; } = "";
    public List<string> RequiredPoints { get; set; } = [];
    public List<string> PartialCredit { get; set; } = [];
    public List<string> CommonErrors { get; set; } = [];
    public string Basis { get; set; } = "";
    public string Confidence { get; set; } = "low";
    public List<string> Sources { get; set; } = [];
    public bool IsValidFor(ExamMeta meta) => SourceYear == meta.Year
        && !string.IsNullOrWhiteSpace(ExpectedAnswer) && !string.IsNullOrWhiteSpace(Basis)
        && Confidence is "high" or "medium" or "low"
        && RequiredPoints is { Count: > 0 } && PartialCredit != null && CommonErrors != null
        && RequiredPoints.Concat(PartialCredit).Concat(CommonErrors).All(s => !string.IsNullOrWhiteSpace(s))
        && Sources is { Count: > 0 } && Sources.All(s => Uri.TryCreate(s, UriKind.Absolute, out var uri) && uri.Scheme is "https" or "http");
    public string Describe() => $"対象年度: {SourceYear} / 確信度: {Confidence}\n正答の骨子: {ExpectedAnswer}\n必須要素:\n・{string.Join("\n・", RequiredPoints)}\n部分点:\n{string.Join("\n", PartialCredit)}\n誤答・減点観点:\n{string.Join("\n", CommonErrors)}\n根拠・推定の区別: {Basis}\n出典:\n{string.Join("\n", Sources)}";
}

/// <summary>
/// 一般的な配点原則とは別に、大学・年度・文理・科目・設問IDで解説と採点基準を索引化する。
/// 問題別基準は常に採点へ渡し、明示的なローカル基準を優先する。旧メモは補助資料として保持。
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

    public QuestionRubric? LookupRubric(ExamMeta meta, string questionId)
    {
        var rubric = items.FirstOrDefault(e => e.Key == KeyFor(meta))?.Notes
            .FirstOrDefault(n => string.Equals(n.QuestionId, questionId, StringComparison.OrdinalIgnoreCase))?.Rubric;
        return rubric?.IsValidFor(meta) == true ? rubric : null;
    }

    public int IndexRubrics(ExamMeta meta, IEnumerable<ProposedQuestion> proposed, IEnumerable<string> questionIds)
    {
        var allowed = questionIds.ToHashSet(StringComparer.OrdinalIgnoreCase);
        var notes = proposed.Where(q => allowed.Contains(q.Id) && q.Rubric?.IsValidFor(meta) == true)
            .GroupBy(q => q.Id, StringComparer.OrdinalIgnoreCase).Where(g => g.Count() == 1)
            .Select(g => new ExplanationNote { QuestionId = g.Key, Rubric = g.Single().Rubric }).ToList();
        Store(meta, notes);
        return notes.Count;
    }

    public string? Lookup(ExamMeta meta, string questionId)
    {
        var entry = items.FirstOrDefault(e => e.Key == KeyFor(meta));
        var note = entry?.Notes.FirstOrDefault(n => string.Equals(n.QuestionId, questionId, StringComparison.OrdinalIgnoreCase));
        return string.IsNullOrWhiteSpace(note?.Notes) ? null : note!.Notes;
    }

    public void Store(ExamMeta meta, IEnumerable<ExplanationNote> notes)
    {
        var fresh = notes.Where(n => (!string.IsNullOrWhiteSpace(n.Notes) || n.Rubric?.IsValidFor(meta) == true) && n.QuestionId.Length > 0).ToList();
        if (fresh.Count == 0) return;
        var entry = items.FirstOrDefault(e => e.Key == KeyFor(meta));
        if (entry == null) { entry = new ExplanationEntry { Key = KeyFor(meta) }; items.Add(entry); }
        entry.ExamLabel = meta.Describe();
        foreach (var note in fresh)
        {
            var previous = entry.Notes.FirstOrDefault(n => string.Equals(n.QuestionId, note.QuestionId, StringComparison.OrdinalIgnoreCase));
            note.Rubric ??= previous?.Rubric;
            if (string.IsNullOrWhiteSpace(note.Notes) && previous != null) { note.Notes = previous.Notes; note.Sources = previous.Sources; }
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
