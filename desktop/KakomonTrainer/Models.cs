using System.Collections.ObjectModel;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace KakomonTrainer;

public class Extensible
{
    [JsonExtensionData] public Dictionary<string, JsonElement>? Extra { get; set; }
}
public class Template : Extensible
{
    public int SchemaVersion { get; set; } = 2;
    public string TemplateId { get; set; } = "";
    public string Title { get; set; } = "";
    // 過去問の特定に使う。登録時にファイル名から自動設定し、編集画面で修正できる。
    public string University { get; set; } = "";
    public int ExamYear { get; set; }
    public string Track { get; set; } = ""; // 文系 / 理系 / 共通 / ""
    public string Subject { get; set; } = "";
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.Now;
    public string SourcePdf { get; set; } = "source.pdf";
    public string BlankPdf { get; set; } = "blank.pdf";
    public int Dpi { get; set; } = 300;
    public List<PageInfo> Pages { get; set; } = [];
    public ObservableCollection<Question> Questions { get; set; } = [];
    public List<string> DefaultRefs { get; set; } = [];
    [JsonIgnore] public int TotalMaxScore => Questions.Sum(q => q.MaxScore);
    [JsonIgnore] public int QuestionCount => Questions.Count;
    [JsonIgnore] public int PageCount => Pages.Count;
}
public class PageInfo : Extensible
{
    public int PageNo { get; set; }
    public int CanvasW { get; set; }
    public int CanvasH { get; set; }
    public List<Marker> Markers { get; set; } = [];
    public List<double[]> QrQuad { get; set; } = [];
}
public class Marker { public int Id { get; set; } public double Cx { get; set; } public double Cy { get; set; } }
public class Region
{
    public int PageNo { get; set; } = 1;
    public double[] Rect { get; set; } = [0, 0, 1, 1];
    public override string ToString() => $"{PageNo}ページ  ({Rect[0]:P0}, {Rect[1]:P0}) → ({Rect[2]:P0}, {Rect[3]:P0})";
}
public class Question : Extensible, System.ComponentModel.INotifyPropertyChanged
{
    public event System.ComponentModel.PropertyChangedEventHandler? PropertyChanged;
    private string id = "";
    public string Id { get => id; set { if (id == value) return; id = value; PropertyChanged?.Invoke(this, new(nameof(Id))); } }
    public string Type { get; set; } = "";
    public int MaxScore { get; set; } = 10;
    public ObservableCollection<Region> Regions { get; set; } = [];
    public List<string> Refs { get; set; } = [];
    public string Note { get; set; } = "";
    public string Language { get; set; } = "ja";
    public string AnswerFormat { get; set; } = "essay";
    public string AnswerKey { get; set; } = "";
    public double AnswerTolerance { get; set; }
    [JsonIgnore] public string ReferencesText { get => string.Join(Environment.NewLine, Refs); set => Refs = value.Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).ToList(); }
}
public class Session : Extensible
{
    public int SchemaVersion { get; set; } = 2;
    public string SessionId { get; set; } = "";
    public string TemplateId { get; set; } = "";
    public string Status { get; set; } = "processing";
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.Now;
    public string OcrBackend { get; set; } = "";
    public List<PageRecord> Pages { get; set; } = [];
    public List<Crop> Crops { get; set; } = [];
    public List<Transcription> Transcriptions { get; set; } = [];
    public List<string> Warnings { get; set; } = [];
    public string Progress { get; set; } = "";
    public string? Error { get; set; }
    [JsonIgnore] public string StatusLabel => Status switch { "processing" => "取り込み中", "ready" => "転記確認待ち", "grading" => "採点中", "graded" => "採点済み", "failed" => "取り込み失敗", _ => Status };
}
public class PageRecord : Extensible
{
    public int PageNo { get; set; }
    public int SourceIndex { get; set; }
    public string NormalizedFilename { get; set; } = "";
    public string TemplateId { get; set; } = "";
    public bool QrDetected { get; set; }
    public string? QrPayload { get; set; }
    public List<int> MarkerIds { get; set; } = [];
    public double? AlignmentErrorPx { get; set; }
}
public class Crop : Extensible
{
    public string QuestionId { get; set; } = "";
    public int Index { get; set; }
    public int PageNo { get; set; }
    public double[] RegionRect { get; set; } = [];
    public double[] CropOriginCanvas { get; set; } = [];
    public int CropW { get; set; }
    public int CropH { get; set; }
    public int MarginPx { get; set; } = 8;
    public string OcrText { get; set; } = "";
    public List<OcrWord> OcrWords { get; set; } = [];
    public string? OcrError { get; set; }
    [JsonIgnore] public string Filename => $"{QuestionId}_{Index}.png";
}
public class OcrWord { public string Text { get; set; } = ""; public int[] Bbox { get; set; } = []; }
public class Transcription : Extensible
{
    public string QuestionId { get; set; } = "";
    public string TranscriptionText { get; set; } = "";
    public bool TranscriptionEdited { get; set; }
    public bool IsBlank { get; set; }
}
public class GradeResult : Extensible
{
    public int SchemaVersion { get; set; } = 2;
    public string SessionId { get; set; } = "";
    public string TemplateId { get; set; } = "";
    public DateTimeOffset GradedAt { get; set; } = DateTimeOffset.Now;
    public string Model { get; set; } = "";
    public string Provider { get; set; } = "";
    public int TotalScore { get; set; }
    public int TotalMaxScore { get; set; }
    public List<QuestionResult> Questions { get; set; } = [];
    public List<string> Warnings { get; set; } = [];
}
public class QuestionResult : Extensible
{
    public string Id { get; set; } = "";
    public int MaxScore { get; set; }
    public int? Score { get; set; }
    public string Transcription { get; set; } = "";
    public bool TranscriptionEdited { get; set; }
    public string Feedback { get; set; } = "";
    public List<Issue> Issues { get; set; } = [];
    public string Confidence { get; set; } = "high";
    public bool SkippedBlank { get; set; }
    public string Grader { get; set; } = "llm";
    public string Model { get; set; } = "";
}
public class Issue
{
    public string Quote { get; set; } = "";
    public string Kind { get; set; } = "その他";
    public string Tag { get; set; } = "その他";
    public string Comment { get; set; } = "";
    public int Deduction { get; set; }
}
public static class Formats
{
    public static Dictionary<string, string> All { get; } = new()
    {
        ["answer_only"] = "答えのみ（自動照合）", ["essay"] = "記述・論述", ["translation_ja"] = "和訳",
        ["translation_en"] = "英訳", ["composition_en"] = "自由英作文", ["math_proof"] = "証明・数式記述",
        ["sci_derivation"] = "計算・導出", ["sci_explanation"] = "論述・理由説明", ["graph"] = "グラフ・作図", ["chem_structure"] = "構造式"
    };
}

// 過去問1件を一意に指す情報。ネット検索と配点キャッシュのキーになる。
public sealed record ExamMeta(string University, int Year, string Track, string Subject)
{
    [JsonIgnore] public bool IsComplete => University.Length > 0 && Year > 0 && Subject.Length > 0;
    [JsonIgnore] public string CacheKey => $"{University}|{Track}|{Subject}";
    public string Describe() => string.Join(" ", new[] { University, Year > 0 ? Year + "年度" : "", Track, Subject }.Where(s => s.Length > 0));
    public static ExamMeta From(Template t) => new(t.University, t.ExamYear, t.Track, t.Subject);
}

// AiService.ProposeStructureAsync の戻り値。submit_structure ツールの出力をそのまま受ける。
public sealed class StructureProposal
{
    public List<ProposedQuestion> Questions { get; set; } = [];
    public List<string> Sources { get; set; } = [];
    public string Confidence { get; set; } = "low";
    public string Summary { get; set; } = "";
    public string AbstractedPrinciple { get; set; } = "";
    public int TypicalTotal { get; set; }
}
public sealed class ProposedQuestion
{
    public string Id { get; set; } = "";
    public string Type { get; set; } = "";
    public int MaxScore { get; set; }
    public string AnswerFormat { get; set; } = "essay";
    // ローカルに解答解説が無い設問の採点補助に使う、ネット情報を要約したメモ。
    public string ExplanationNotes { get; set; } = "";
    public List<string> ExplanationSources { get; set; } = [];
}
