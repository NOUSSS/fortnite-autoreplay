// Lecteur de replays de Fortnite Auto Replay : pour chaque replay donné en argument,
// une ligne JSON sur la sortie standard avec les coffres ouverts par le joueur qui l'a
// enregistré. L'analyse est celle de ChestCounter (vendor/ChestCounter/ReplayAnalyzer.cs).
using System.Text.Json;
using ChestCounter;

if (args.Length == 0 || args.Contains("--help"))
{
    Console.Error.WriteLine("usage : replay-reader <fichier.replay>…  |  replay-reader --version");
    return 2;
}
if (args.Contains("--version"))
{
    Console.Out.WriteLine(ReplayAnalyzer.CurrentVersion);
    return 0;
}

foreach (var path in args)
{
    var name = Path.GetFileName(path);
    object line;
    try
    {
        byte[] bytes;
        // Fortnite peut encore tenir le fichier ouvert : lecture partagée.
        using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
        {
            bytes = new byte[fs.Length];
            fs.ReadExactly(bytes);
        }
        using var ms = new MemoryStream(bytes, writable: false);
        var match = new ReplayAnalyzer().Analyze(ms, name, "");
        line = new
        {
            file = name,
            ok = true,
            session_id = match.SessionId,
            // heure UTC, marquée comme telle (Kind non précisé par l'analyse)
            start = match.MatchStartUtc is DateTime at ? DateTime.SpecifyKind(at, DateTimeKind.Utc) : (DateTime?)null,
            duration_s = match.DurationSeconds,
            playlist = match.Playlist,
            battle_royale = match.IsBattleRoyale,
            chests = match.ChestsOpened,
            destroyed = match.Chests.Count(c => c.Destroyed),
            ammo_boxes = match.AmmoBoxesOpened,
            analyzer = match.AnalyzerVersion,
        };
    }
    catch (Exception ex)
    {
        var inner = (ex as AggregateException)?.InnerException ?? ex;
        line = new { file = name, ok = false, error = $"{inner.GetType().Name}: {inner.Message}" };
    }
    Console.Out.WriteLine(JsonSerializer.Serialize(line));
    Console.Out.Flush();
}
return 0;
