using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization.Metadata;

namespace KakomonTrainer;

public sealed class DataStore
{
    public static readonly JsonSerializerOptions Json = CreateOptions();
    private static JsonSerializerOptions CreateOptions()
    {
        var resolver = new DefaultJsonTypeInfoResolver();
        resolver.Modifiers.Add(info =>
        {
            if (info.Type == typeof(Transcription))
                info.Properties.Single(p => p.Name == "transcription_text").Name = "transcription";
        });
        return new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower, PropertyNameCaseInsensitive = true, WriteIndented = true, TypeInfoResolver = resolver, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping };
    }
    public string Root { get; }
    public List<string> LoadWarnings { get; } = [];
    public DataStore(string root)
    {
        Root = Path.GetFullPath(root);
        foreach (var d in new[] { "templates", "scans", "results", "logs/review" }) Directory.CreateDirectory(Path.Combine(Root, d));
    }
    public static string Child(string root, string relative)
    {
        var full = Path.GetFullPath(Path.Combine(root, relative));
        if (!full.StartsWith(Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("保存先の外を参照するパスは使用できません。");
        return full;
    }
    public static void ValidateId(string id)
    {
        if (string.IsNullOrWhiteSpace(id) || id != id.Trim() || id.EndsWith('.') || id.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || id is "." or ".." || System.Text.RegularExpressions.Regex.IsMatch(id, @"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", System.Text.RegularExpressions.RegexOptions.IgnoreCase))
            throw new InvalidDataException($"使用できない管理IDです: {id}");
    }
    public string TemplateDir(string id) { ValidateId(id); return Child(Path.Combine(Root, "templates"), id); }
    public string SessionDir(string id) { ValidateId(id); return Child(Path.Combine(Root, "scans"), id); }
    public string ResultDir(string id) { ValidateId(id); return Child(Path.Combine(Root, "results"), id); }
    public string NormalizedPath(Session session, PageRecord page) => Child(SessionDir(session.SessionId), page.NormalizedFilename.Contains('/') || page.NormalizedFilename.Contains('\\') ? page.NormalizedFilename : "normalized/" + page.NormalizedFilename);
    public static T Read<T>(string path) => JsonSerializer.Deserialize<T>(File.ReadAllText(path), Json) ?? throw new InvalidDataException($"データを読み込めません: {path}");
    public static void Write<T>(string path, T value)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temp = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try { File.WriteAllText(temp, JsonSerializer.Serialize(value, Json)); File.Move(temp, path, true); }
        finally { if (File.Exists(temp)) File.Delete(temp); }
    }
    public Template LoadTemplate(string id)
    {
        var t = Read<Template>(Path.Combine(TemplateDir(id), "template.json"));
        if (t.SchemaVersion != 2) throw new InvalidDataException($"{id}: 用紙形式 v{t.SchemaVersion} は非対応です。元PDFから再登録してください。");
        foreach (var q in t.Questions) if (q.AnswerFormat is "written" or "") q.AnswerFormat = "essay";
        return t;
    }
    public List<Template> Templates()
    {
        LoadWarnings.Clear();
        var items = new List<Template>();
        foreach (var path in Directory.GetFiles(Path.Combine(Root, "templates"), "template.json", SearchOption.AllDirectories))
            try { items.Add(LoadTemplate(Path.GetFileName(Path.GetDirectoryName(path))!)); }
            catch (Exception ex) { LoadWarnings.Add(ex.Message); }
        return items.OrderByDescending(t => t.CreatedAt).ToList();
    }
    public void SaveTemplate(Template t)
    {
        ValidateId(t.TemplateId);
        if (t.Questions.Select(q => q.Id).Distinct(StringComparer.OrdinalIgnoreCase).Count() != t.Questions.Count) throw new InvalidDataException("設問IDが重複しています。");
        foreach (var q in t.Questions)
        {
            ValidateId(q.Id);
            if (q.MaxScore < 0 || !double.IsFinite(q.AnswerTolerance) || q.AnswerTolerance < 0) throw new InvalidDataException("配点と許容誤差は0以上で指定してください。");
            if (!Formats.All.ContainsKey(q.AnswerFormat)) throw new InvalidDataException("採点形式を選択してください。");
            foreach (var r in q.Regions)
                if (!t.Pages.Any(p => p.PageNo == r.PageNo) || r.Rect.Length != 4 || r.Rect.Any(v => !double.IsFinite(v) || v < 0 || v > 1) || r.Rect[2] <= r.Rect[0] || r.Rect[3] <= r.Rect[1]) throw new InvalidDataException($"{q.Id}: 解答領域が不正です。");
            foreach (var reference in q.Refs) if (!File.Exists(Child(TemplateDir(t.TemplateId), reference)) && !Directory.Exists(Child(TemplateDir(t.TemplateId), reference))) throw new InvalidDataException($"参照資料がありません: {reference}");
        }
        Write(Path.Combine(TemplateDir(t.TemplateId), "template.json"), t);
    }
    public List<Session> Sessions() => Directory.GetFiles(Path.Combine(Root, "scans"), "session.json", SearchOption.AllDirectories).Select(p =>
    {
        try { return Read<Session>(p); } catch (Exception ex) { LoadWarnings.Add($"{p}: {ex.Message}"); return null; }
    }).OfType<Session>().OrderByDescending(s => s.CreatedAt).ToList();
    public void SaveSession(Session s) => Write(Path.Combine(SessionDir(s.SessionId), "session.json"), s);
    public GradeResult? Result(Session s) => s.Status == "graded" && File.Exists(Path.Combine(ResultDir(s.SessionId), "result.json")) ? Read<GradeResult>(Path.Combine(ResultDir(s.SessionId), "result.json")) : null;
    public void SaveTranscriptions(Session s)
    {
        s.Status = "ready"; s.Error = null;
        SaveSession(s); // Results are exposed only while the session is graded.
    }
}
