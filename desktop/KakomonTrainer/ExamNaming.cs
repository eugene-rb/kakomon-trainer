using System.IO;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace KakomonTrainer;

/// <summary>
/// 解答用紙のファイル名（共通名の部分）から大学・年度・文理・科目を推定する。
/// 命名規則: <c>&lt;大学&gt;_&lt;年度&gt;_&lt;文理&gt;_&lt;科目&gt;_sheet.pdf</c>
/// 例: <c>kyodai_2021_rikei_kagaku_sheet.pdf</c> / <c>07kyoto_21_zenki_kagaku_sheet.pdf</c>
/// 既定の別名表に加えて <c>%LOCALAPPDATA%/KakomonTrainer/exam-aliases.json</c> を上書きマージする。
/// </summary>
public static class ExamNaming
{
    private static readonly Dictionary<string, string> Universities = new(StringComparer.OrdinalIgnoreCase)
    {
        ["kyodai"] = "京都大学", ["kyoto"] = "京都大学", ["京大"] = "京都大学",
        ["todai"] = "東京大学", ["tokyo"] = "東京大学", ["東大"] = "東京大学",
        ["hokudai"] = "北海道大学", ["hokkaido"] = "北海道大学", ["北大"] = "北海道大学",
        ["tohoku"] = "東北大学", ["tohokudai"] = "東北大学", ["東北大"] = "東北大学",
        ["nagoya"] = "名古屋大学", ["meidai"] = "名古屋大学", ["名大"] = "名古屋大学",
        ["osaka"] = "大阪大学", ["handai"] = "大阪大学", ["阪大"] = "大阪大学",
        ["kyushu"] = "九州大学", ["kyudai"] = "九州大学", ["九大"] = "九州大学",
        ["hitotsubashi"] = "一橋大学", ["一橋"] = "一橋大学",
        ["titech"] = "東京工業大学", ["tokodai"] = "東京工業大学", ["東工大"] = "東京工業大学",
        ["kobe"] = "神戸大学", ["shindai"] = "神戸大学",
        ["tsukuba"] = "筑波大学", ["chiba"] = "千葉大学", ["yokokoku"] = "横浜国立大学",
        ["waseda"] = "早稲田大学", ["souda"] = "早稲田大学", ["早大"] = "早稲田大学",
        ["keio"] = "慶應義塾大学", ["keiou"] = "慶應義塾大学", ["慶應"] = "慶應義塾大学", ["慶大"] = "慶應義塾大学",
    };
    private static readonly Dictionary<string, string> Subjects = new(StringComparer.OrdinalIgnoreCase)
    {
        ["eigo"] = "英語", ["english"] = "英語", ["en"] = "英語",
        ["sugaku"] = "数学", ["suugaku"] = "数学", ["math"] = "数学", ["ma"] = "数学",
        ["kokugo"] = "国語", ["japanese"] = "国語", ["jp"] = "国語", ["kobun"] = "国語",
        ["butsuri"] = "物理", ["physics"] = "物理",
        ["kagaku"] = "化学", ["chemistry"] = "化学",
        ["seibutsu"] = "生物", ["biology"] = "生物",
        ["chigaku"] = "地学", ["earth"] = "地学",
        ["rika"] = "理科", ["science"] = "理科", ["sn"] = "理科",
        ["nihonshi"] = "日本史", ["sekaishi"] = "世界史", ["chiri"] = "地理",
        ["seikei"] = "政治経済", ["rinri"] = "倫理", ["gendaishakai"] = "現代社会",
        ["shoron"] = "小論文", ["ronbun"] = "小論文",
    };
    private static readonly Dictionary<string, string> Tracks = new(StringComparer.OrdinalIgnoreCase)
    {
        ["文系"] = "文系", ["文"] = "文系", ["bunkei"] = "文系", ["bun"] = "文系", ["liberal"] = "文系",
        ["理系"] = "理系", ["理"] = "理系", ["rikei"] = "理系", ["ri"] = "理系", ["sci"] = "理系",
        ["共通"] = "共通", ["kyotsu"] = "共通", ["kyotu"] = "共通", ["common"] = "共通",
    };

    /// <summary>ファイル名の共通名から <see cref="ExamMeta"/> を推定する。第2要素は判定できなかった項目名。</summary>
    public static (ExamMeta Meta, List<string> Unresolved) Parse(string? stem)
    {
        var (universities, subjects, tracks) = WithUserAliases();
        var tokens = Regex.Split(stem ?? "", @"[_\-\s.]+").Where(t => t.Length > 0).ToList();
        var unresolved = new List<string>();

        var yearIndex = -1;
        var year = 0;
        for (var i = 0; i < tokens.Count; i++)
        {
            if (!Regex.IsMatch(tokens[i], @"^\d{2}$|^(19|20)\d{2}$")) continue;
            var n = int.Parse(tokens[i]);
            year = tokens[i].Length <= 2 ? 2000 + n : n;
            yearIndex = i;
            break;
        }
        if (year is < 1990 or > 2100) year = 0;
        if (year == 0) unresolved.Add("年度");

        var before = yearIndex >= 0 ? tokens.Take(yearIndex).ToList() : new List<string>(tokens);
        var after = yearIndex >= 0 ? tokens.Skip(yearIndex + 1).ToList() : new List<string>();

        var university = ResolveUniversity(before, universities);
        if (university.Length == 0) unresolved.Add("大学");

        var track = "";
        for (var i = after.Count - 1; i >= 0; i--)
        {
            if (!tracks.TryGetValue(after[i], out var value)) continue;
            track = value;
            after.RemoveAt(i);
            break;
        }

        var subject = "";
        foreach (var token in after)
            if (subjects.TryGetValue(token, out var value)) { subject = value; break; }
        if (subject.Length == 0 && after.Count > 0) subject = after[^1];
        if (subject.Length == 0) unresolved.Add("科目");

        return (new ExamMeta(university, year, track, subject), unresolved);
    }

    private static string ResolveUniversity(List<string> tokens, Dictionary<string, string> table)
    {
        if (tokens.Count == 0) return "";
        var candidates = new List<string> { string.Concat(tokens) };
        candidates.AddRange(tokens);
        candidates.AddRange(tokens.Select(t => Regex.Replace(t, @"^\d+", ""))); // "07kyoto" -> "kyoto"
        foreach (var candidate in candidates)
            if (candidate.Length > 0 && table.TryGetValue(candidate, out var name)) return name;
        foreach (var candidate in candidates)
            if (candidate.EndsWith("大学")) return candidate;
        return "";
    }

    private static (Dictionary<string, string> Universities, Dictionary<string, string> Subjects, Dictionary<string, string> Tracks) WithUserAliases()
    {
        var universities = new Dictionary<string, string>(Universities, StringComparer.OrdinalIgnoreCase);
        var subjects = new Dictionary<string, string>(Subjects, StringComparer.OrdinalIgnoreCase);
        var tracks = new Dictionary<string, string>(Tracks, StringComparer.OrdinalIgnoreCase);
        var path = Path.Combine(AppSettings.ConfigRoot, "exam-aliases.json");
        if (!File.Exists(path)) return (universities, subjects, tracks);
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            Merge(document.RootElement, "universities", universities);
            Merge(document.RootElement, "subjects", subjects);
            Merge(document.RootElement, "tracks", tracks);
        }
        catch (Exception ex)
        {
            throw new InvalidDataException($"exam-aliases.json を読み込めません（{path}）: {ex.Message}");
        }
        return (universities, subjects, tracks);

        static void Merge(JsonElement root, string name, Dictionary<string, string> into)
        {
            if (!root.TryGetProperty(name, out var section) || section.ValueKind != JsonValueKind.Object) return;
            foreach (var pair in section.EnumerateObject())
                if (pair.Value.ValueKind == JsonValueKind.String) into[pair.Name] = pair.Value.GetString() ?? "";
        }
    }
}
