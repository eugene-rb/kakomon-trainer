using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace KakomonTrainer;

public static class AnswerMatcher
{
    public static string Normalize(string text) => Regex.Replace(text.Normalize(NormalizationForm.FormKC).ToLowerInvariant().Replace('−', '-').Replace('’', '\''), @"\s+", "").TrimEnd('.', '。');
    private static (double Number, string Unit)? Number(string text)
    {
        const string superscript = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻";
        text = Regex.Replace(text, "[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+", m => "^" + string.Concat(m.Value.Select(c => "0123456789+-"[superscript.IndexOf(c)])));
        var s = Normalize(text).Replace(",", ""); var percent = s.EndsWith('%'); if (percent) s = s[..^1];
        double value;
        var sci = Regex.Match(s, @"^([+-]?(?:\d+\.?\d*|\.\d+))?[×x*]10\^?([+-]?\d+)$");
        if (sci.Success) value = (sci.Groups[1].Success ? double.Parse(sci.Groups[1].Value, CultureInfo.InvariantCulture) : 1) * Math.Pow(10, int.Parse(sci.Groups[2].Value));
        else if (Regex.IsMatch(s, @"^10\^[+-]?\d+$")) value = Math.Pow(10, int.Parse(s[3..]));
        else if (s.Split('/') is [var a, var b] && double.TryParse(a, CultureInfo.InvariantCulture, out var numerator) && double.TryParse(b, CultureInfo.InvariantCulture, out var denominator) && denominator != 0) value = numerator / denominator;
        else
        {
            var match = Regex.Match(s, @"^([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)(.*)$");
            if (!match.Success || !double.TryParse(match.Groups[1].Value, CultureInfo.InvariantCulture, out value) || !double.IsFinite(value)) return null;
            return (percent ? value / 100 : value, match.Groups[2].Value);
        }
        return double.IsFinite(value) ? (percent ? value / 100 : value, "") : null;
    }
    // null means a human or LLM must resolve the answer, never silently award zero.
    public static bool? Judge(string key, string given, double tolerance)
    {
        var keys = key.Split(['\r', '\n', '|', '｜'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (keys.Length == 0) return null;
        if (string.IsNullOrWhiteSpace(given)) return false;
        if (keys.Any(k => Normalize(k) == Normalize(given))) return true;
        var number = Number(given); var expected = keys.Select(Number).ToArray();
        if (number.HasValue && expected.All(n => n.HasValue))
        {
            var same = expected.Where(n => n!.Value.Unit == number.Value.Unit).ToArray();
            if (same.Length > 0) return same.Any(n => Math.Abs(n!.Value.Number - number.Value.Number) <= Math.Max(tolerance, 1e-9));
        }
        return null;
    }
}
public sealed class GradingService(DataStore store, PdfService pdf, AiService ai, AppSettings settings)
{
    public async Task<GradeResult> GradeAsync(Template template, Session session, IProgress<string> progress, CancellationToken token)
    {
        if (session.Status is "processing" or "grading" or "failed") throw new InvalidOperationException("取り込み済みの答案を選んでください。");
        if (template.Questions.Any(q => !session.Transcriptions.Any(t => t.QuestionId == q.Id))) throw new InvalidDataException("用紙の設問が変更されています。答案を取り込み直してください。");
        session.Status = "grading"; session.Error = null; store.SaveSession(session);
        var result = new GradeResult { TemplateId = template.TemplateId, SessionId = session.SessionId, Provider = settings.Provider, Model = settings.Model, TotalMaxScore = template.TotalMaxScore };
        try
        {
            foreach (var q in template.Questions)
            {
                token.ThrowIfCancellationRequested(); progress.Report($"{q.Id} を採点中… ({result.Questions.Count + 1}/{template.QuestionCount})");
                var transcription = session.Transcriptions.Single(t => t.QuestionId == q.Id);
                QuestionResult answer;
                try
                {
                    if (transcription.IsBlank) answer = new QuestionResult { Score = 0, SkippedBlank = true, Grader = "deterministic", Model = "blank", Feedback = "未記入として採点しました。" };
                    else if (q.AnswerFormat == "answer_only")
                    {
                        var tolerance = q.AnswerTolerance > 0 ? q.AnswerTolerance : double.TryParse(settings.Get("ANSWER_ONLY_NUMERIC_TOLERANCE", "0"), CultureInfo.InvariantCulture, out var defaultTolerance) ? defaultTolerance : 0;
                        var verdict = AnswerMatcher.Judge(q.AnswerKey, transcription.TranscriptionText, tolerance);
                        answer = new QuestionResult { Score = verdict.HasValue ? verdict.Value ? q.MaxScore : 0 : null, Grader = "deterministic", Model = "answer-match", Confidence = verdict.HasValue ? "high" : "low", Feedback = verdict switch { true => "正答です。", false => $"正答: {q.AnswerKey}", null => "表記差を自動確定できません。結果画面で点数を確認・確定してください。" } };
                        if (verdict == false) answer.Issues.Add(new Issue { Quote = transcription.TranscriptionText, Tag = "誤答", Deduction = q.MaxScore, Comment = $"正答: {q.AnswerKey}" });
                        if (verdict == null && !string.IsNullOrWhiteSpace(q.AnswerKey) && settings.Get("ANSWER_ONLY_LLM_FALLBACK", "true") == "true" && settings.Get("ANTHROPIC_API_KEY").Length > 0)
                            answer = await ai.CheckAnswer(session, q, transcription, token);
                    }
                    else
                    {
                        answer = await ai.GradeQuestion(template, session, q, transcription, token);
                        if (settings.Get("DOUBLE_GRADING", "false") == "true")
                        {
                            var second = await ai.GradeQuestion(template, session, q, transcription, token);
                            var threshold = int.TryParse(settings.Get("DOUBLE_GRADING_THRESHOLD", "3"), out var configured) ? configured : 3;
                            if (Math.Abs((answer.Score ?? 0) - (second.Score ?? 0)) >= threshold)
                            {
                                answer.Feedback += $"\n二重採点の差が大きいため要確認です（{answer.Score}点 / {second.Score}点）。";
                                answer.Score = null; answer.Confidence = "low";
                            }
                        }
                    }
                }
                catch (Exception ex) when (ex is not OperationCanceledException) { answer = new QuestionResult { Score = null, Confidence = "low", Feedback = "採点に失敗しました: " + ex.Message }; }
                answer.Id = q.Id; answer.MaxScore = q.MaxScore; answer.Transcription = transcription.TranscriptionText; answer.TranscriptionEdited = transcription.TranscriptionEdited;
                result.Questions.Add(answer);
            }
            await SaveResult(template, session, result, token); return result;
        }
        catch (Exception ex) { session.Status = "ready"; session.Error = ex is OperationCanceledException ? "採点を中止しました。" : ex.Message; store.SaveSession(session); throw; }
    }
    public async Task SaveResult(Template t, Session s, GradeResult result, CancellationToken token)
    {
        s.Status = "grading"; store.SaveSession(s);
        try { await SaveResultFiles(t, s, result, token); }
        catch
        {
            s.Status = "ready"; s.Error = "結果の保存に失敗しました。再実行してください。"; store.SaveSession(s); throw;
        }
    }
    private async Task SaveResultFiles(Template t, Session s, GradeResult result, CancellationToken token)
    {
        result.TotalScore = result.Questions.Sum(q => q.Score ?? 0); result.GradedAt = DateTimeOffset.Now;
        result.Warnings = result.Questions.Where(q => q.Score == null).Select(q => $"{q.Id}: 未確定（合計に含めていません）").ToList();
        var dir = store.ResultDir(s.SessionId); Directory.CreateDirectory(dir);
        var tempPdf = Path.Combine(dir, "graded.pending.pdf");
        try { await pdf.ExportGradedAsync(store, t, s, result, tempPdf, token); File.Move(tempPdf, Path.Combine(dir, "graded.pdf"), true); }
        finally { if (File.Exists(tempPdf)) File.Delete(tempPdf); }
        DataStore.Write(Path.Combine(dir, "result.json"), result);
        var log = Path.Combine(store.Root, "logs", "review", s.SessionId + ".md");
        await File.WriteAllTextAsync(log, PdfService.ReviewText(result), token);
        // One JSON object per line, one file per session: regrading replaces the entry.
        var compact = new System.Text.Json.JsonSerializerOptions(DataStore.Json) { WriteIndented = false };
        await File.WriteAllTextAsync(Path.Combine(store.Root, "logs", "review", s.SessionId + ".jsonl"), System.Text.Json.JsonSerializer.Serialize(result, compact) + "\n", token);
        s.Status = "graded"; s.Error = null; store.SaveSession(s);
    }
}
