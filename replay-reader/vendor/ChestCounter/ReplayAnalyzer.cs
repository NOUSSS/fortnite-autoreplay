using System.Text.RegularExpressions;
using FortniteReplayReader;
using FortniteReplayReader.Models;
using FortniteReplayReader.Models.NetFieldExports;
using FortniteReplayReader.Models.NetFieldExports.Items;
using FortniteReplayReader.Models.NetFieldExports.Weapons;
using Unreal.Core;
using Unreal.Core.Contracts;
using Unreal.Core.Models;
using Unreal.Core.Models.Enums;

namespace ChestCounter;

/// <summary>
/// Reads one .replay file and extracts every container searched by the tracked player,
/// together with the items that were tossed out of it.
/// </summary>
public sealed class ReplayAnalyzer : FortniteReplayReader.ReplayReader
{
    /// <summary>Stale = state received with the channel itself (searched before it became relevant to the recorder).</summary>
    private sealed record SearchEvent(float Time, string ActorKey, string Label, uint? SearcherPawn, bool Destroyed = false, bool Stale = false);
    private sealed record TossEvent(float Time, string Item);
    private sealed record EquipEvent(float Time, string Item, uint Pawn);
    private sealed record PrizeEvent(float Time, string PrizeId, uint? Pawn, string Container);

    private static readonly Regex PrizeIdPattern = new(@"[A-Za-z][A-Za-z0-9_]*:[A-Za-z0-9_.\-]+", RegexOptions.Compiled);

    // Treasure hunt items do not drop on the ground: they go straight into the inventory.
    // They only show up in the replay when the player equips them (a weapon/gizmo actor is spawned).
    private static readonly Regex TreasureActor = new(
        @"FlameCeleb|RelicExtractor|RelicFinder|LuckyLocator|SmugglerExtractionKey|ExtractionAccelerat",
        RegexOptions.Compiled | RegexOptions.IgnoreCase);

    private static readonly Regex UaidSuffix = new(@"_UAID_.*$", RegexOptions.Compiled);

    // Items that are part of the treasure hunt "Standard" prize pool, or anything that looks like a treasure.
    private static readonly Regex TreasureItem = new(
        @"RelicExtractor|RelicFinder|LuckyLocator|FlameCeleb|SmugglerExtractionKey|ExtractionAccelerat|Treasure|PirateShop|DelilahShop",
        RegexOptions.Compiled | RegexOptions.IgnoreCase);

    // Limited-time event loot ("happy hours"): a portable extractor that simply drops from chests. It looks like the
    // treasure hunt gizmo (WID_RelicExtractor) but is not a treasure: no prize handler is granted for it.
    private static readonly Regex EventItem = new(@"RelicExtractor_Consumable", RegexOptions.Compiled | RegexOptions.IgnoreCase);

    static ReplayAnalyzer()
    {
        // Map every chest / container blueprint we do not know yet onto the generic container group.
        NetFieldParser.AliasResolver = path =>
        {
            if (path.Contains(':') || path.EndsWith("_ClassNetCache", StringComparison.Ordinal))
            {
                return null;
            }

            var last = path[(path.LastIndexOf('/') + 1)..];

            // Variants of the golden treasure chest animation (e.g. BGA_DelilahTreasureHuntAnimatedChest_Rare) carry the
            // prize id too: map them onto TreasureHuntChest, not GenericContainer (their name contains "Chest").
            if (last.StartsWith("BGA_DelilahTreasureHuntAnimatedChest", StringComparison.Ordinal))
            {
                return "/DelilahTreasureHuntGameplay/Gameplay/Actors/BGA_DelilahTreasureHuntAnimatedChest.BGA_DelilahTreasureHuntAnimatedChest_C";
            }

            if (last.StartsWith("B_", StringComparison.Ordinal) && TreasureActor.IsMatch(last))
            {
                return GenericWeapon.GroupPath;
            }

            return path.Contains("/Containers/", StringComparison.OrdinalIgnoreCase)
                   || last.Contains("Chest", StringComparison.OrdinalIgnoreCase)
                ? GenericContainer.GroupPath
                : null;
        };

        // Loot cannons: every cannon blueprint exposes the FireLootCannon RPC through its own ClassNetCache.
        NetFieldParser.ClassNetCacheAliasResolver = path =>
            path.Contains("LootCannon", StringComparison.OrdinalIgnoreCase) && path.EndsWith("_ClassNetCache", StringComparison.Ordinal)
                ? LootCannonCache.Path
                : null;
    }

    private readonly Dictionary<uint, string> _channelKey = new();
    private readonly Dictionary<uint, string> _channelLabel = new();
    private readonly Dictionary<uint, uint> _lastSearcher = new();
    private readonly HashSet<string> _searched = new();
    private readonly List<SearchEvent> _searches = new();
    /// <summary>When each channel was opened (a container state received right then is "stale").</summary>
    private readonly Dictionary<uint, float> _channelOpenAt = new();
    /// <summary>Searched state of each open container channel (false again when it is refilled).</summary>
    private readonly Dictionary<uint, bool> _channelSearched = new();
    /// <summary>Who made the last recorded search of each container key.</summary>
    private readonly Dictionary<string, uint?> _lastOpener = new();
    private static readonly string? Debug = Environment.GetEnvironmentVariable("CHESTCOUNTER_DEBUG");

    /// <summary>Level path or spawn location: the same container keeps the same key all match long.</summary>
    private static bool StableKey(string key) => !key.StartsWith("guid:", StringComparison.Ordinal) && !key.StartsWith("ch:", StringComparison.Ordinal);

    /// <summary>A chest spawned during the match (not the sand pile left by a buried chest, nor any other prop).</summary>
    private static bool IsDynamicChest(string label) =>
        !label.StartsWith("Prop_", StringComparison.OrdinalIgnoreCase) && ChestKinds.IsChest(ChestKinds.Classify(label));
    private readonly List<TossEvent> _tosses = new();
    private readonly List<EquipEvent> _equips = new();
    private readonly List<PrizeEvent> _prizes = new();
    private readonly Dictionary<string, float> _firstExport = new();
    private static readonly Regex TrackedExport = new(@"TreasureHandler|WID_(FlameCeleb|RelicExtractor|RelicFinder|LuckyLocator|SmugglerExtractionKey)", RegexOptions.Compiled);
    private readonly HashSet<uint> _prizeChannelsSeen = new();
    private readonly HashSet<uint> _equipChannelsSeen = new();
    private readonly Dictionary<uint, uint> _pawnToPlayerState = new();
    private readonly Dictionary<uint, int> _playerStateToPlayerId = new();

    // Treasure prize handler abilities ("GA_TreasureHandler_*"): class netguid -> name.
    private readonly Dictionary<uint, string> _treasureHandlerGuids = new();

    /// <summary>One treasure prize handler granted to a player (one per treasure obtained).</summary>
    private sealed record GrantEvent(float Time, uint PlayerStateActor, int ElementId, string Handler);
    private readonly List<GrantEvent> _grants = new();

    /// <summary>Bump when the analysis changes so that existing replays are re-analyzed.</summary>
    public const int CurrentVersion = 15;

    private static readonly bool ProbeDebug = Environment.GetEnvironmentVariable("CHESTCOUNTER_DEBUG") == "4";

    public ReplayAnalyzer() : base(Environment.GetEnvironmentVariable("CHESTCOUNTER_DEBUG") == "2" ? new StderrLogger() : null,
        Enum.TryParse<ParseMode>(Environment.GetEnvironmentVariable("CHESTCOUNTER_MODE"), out var mode) ? mode : ParseMode.Normal)
    {
        // The ability system component of the player state is not decoded by the library (its fields have no
        // exported names): its raw FastArray payloads are read here to find every treasure handler granted.
        Regex? debugFilter = null;
        if (ProbeDebug)
        {
            debugFilter = new Regex(Environment.GetEnvironmentVariable("CHESTCOUNTER_PROBE") is { Length: > 0 } p
                ? p : "Treasure|Prize|Magpie|Inventory|Quest|Reward|Hunt|PlayerController|PlayerState|Notif", RegexOptions.IgnoreCase);
            NetGuidCache.KeepOnCleanup = true;
        }
        RawProbe.Filter = path => path.Contains("FortAbilitySystemComponent", StringComparison.Ordinal) || debugFilter?.IsMatch(path) == true;
        RawProbe.Sink = (channel, group, field, data, bits) =>
        {
            // FortAbilitySystemComponent_ClassNetCache (unnamed fields) or FortAbilitySystemComponentAthena_ClassNetCache
            // (named "ActivatableAbilities", handed over raw by the reader instead of its generic NetDelta decoding).
            if (group.Contains("AbilitySystemComponent", StringComparison.Ordinal) && group.EndsWith("_ClassNetCache", StringComparison.Ordinal)
                && field is "?" or "ActivatableAbilities" && _treasureHandlerGuids.Count > 0)
            {
                ReadAbilityGrants(channel, data, bits);
            }
            if (debugFilter?.IsMatch(group + "." + field) == true)
            {
                ProbeSink(channel, group, field, data, bits);
            }
        };
    }

    /// <summary>
    /// Decodes a FastArray delta (ActivatableAbilities) without knowing the field names: header, then for each
    /// changed element its id and its properties (handle, bit count, payload). An element whose object reference
    /// is a treasure handler class means a treasure was given to the owner of this ability system.
    /// </summary>
    private void ReadAbilityGrants(uint channel, byte[] data, int bits)
    {
        var pos = 1; // bSupportsFastArrayDeltaStructSerialization
        int Bit() => pos < bits ? (data[pos >> 3] >> (pos & 7)) & 1 : throw new IndexOutOfRangeException();
        uint Read(int n) { uint v = 0; for (var k = 0; k < n; k++) { v |= (uint)Bit() << k; pos++; } return v; }
        uint ReadPacked()
        {
            uint v = 0;
            for (var shift = 0; shift < 35; shift += 7)
            {
                var b = Read(8);
                v |= (b >> 1) << shift;
                if ((b & 1) == 0) return v;
            }
            throw new InvalidDataException();
        }

        try
        {
            _ = Read(32); _ = Read(32); // replication keys
            var deletes = Read(32);
            var changed = Read(32);
            if (deletes > 512 || changed > 512 || bits < 129)
            {
                return;
            }
            pos += (int)deletes * 32;
            for (var i = 0; i < changed; i++)
            {
                var elementId = (int)Read(32);
                while (true)
                {
                    var handle = ReadPacked();
                    if (handle == 0) break;
                    var numBits = (int)ReadPacked();
                    if (numBits < 0 || pos + numBits > bits) return;
                    if (numBits is 8 or 16 or 24 or 32 or 40)
                    {
                        var start = pos;
                        uint guid = 0;
                        for (var shift = 0; pos < start + numBits; shift += 7)
                        {
                            var b = Read(8);
                            guid |= (b >> 1) << shift;
                            if ((b & 1) == 0) break;
                        }
                        pos = start + numBits;
                        if (_treasureHandlerGuids.TryGetValue(guid, out var handler))
                        {
                            var actor = Channels[channel]?.ActorId ?? 0;
                            if (!_grants.Any(g => g.PlayerStateActor == actor && g.ElementId == elementId))
                            {
                                _grants.Add(new GrantEvent(Now, actor, elementId, handler));
                            }
                        }
                    }
                    else
                    {
                        pos += numBits;
                    }
                }
            }
        }
        catch (Exception e) when (e is IndexOutOfRangeException or InvalidDataException)
        {
            // Not a FastArray delta (or another layout): ignore.
            if (ProbeDebug) Console.Error.WriteLine($"GRANTFAIL {Now,7:F1}s ch{channel} bits={bits} pos={pos} {e.GetType().Name}");
        }
    }

    /// <summary>Debug 4: prints undecoded payloads with any readable text / object reference they contain.</summary>
    private void ProbeSink(uint channel, string group, string field, byte[] data, int bits)
    {
        var strings = new HashSet<string>();
        for (var shift = 0; shift < 8; shift++)
        {
            var sb = new System.Text.StringBuilder();
            for (var i = 0; i + 1 <= (bits - shift) / 8; i++)
            {
                var bitPos = i * 8 + shift;
                var b = (data[bitPos / 8] >> (bitPos % 8)) | (bitPos / 8 + 1 < data.Length ? data[bitPos / 8 + 1] << (8 - bitPos % 8) : 0);
                var c = (char)(b & 0xFF);
                if (char.IsAsciiLetterOrDigit(c) || c is '_' or ':' or '.' or '/' or '-' or ' ')
                {
                    sb.Append(c);
                    continue;
                }
                if (sb.Length >= 5) strings.Add(sb.ToString().Trim());
                sb.Clear();
            }
            if (sb.Length >= 5) strings.Add(sb.ToString().Trim());
        }

        // Single object reference (packed int netguid)?
        var guidText = "";
        if (bits <= 48)
        {
            uint value = 0;
            var shiftBits = 0;
            foreach (var by in data)
            {
                value |= (uint)(by >> 1) << shiftBits;
                shiftBits += 7;
                if ((by & 1) == 0 || shiftBits > 28) break;
            }
            var path = PathOf(value);
            if (!string.IsNullOrEmpty(path)) guidText = $" guid={value}:{CleanName(path)}";
        }

        // Brute force: every bit offset decoded as a packed netguid that resolves to a known object path.
        if (bits > 48 && Environment.GetEnvironmentVariable("CHESTCOUNTER_PROBE_GUIDS") == "1")
        {
            var refs = new List<string>();
            int Bit(int pos) => pos < bits ? (data[pos >> 3] >> (pos & 7)) & 1 : 0;
            for (var start = 0; start + 8 <= bits; start++)
            {
                uint value = 0;
                var pos = start;
                for (var n = 0; n < 5 && pos + 8 <= bits; n++)
                {
                    var by = 0;
                    for (var k = 0; k < 8; k++) by |= Bit(pos++) << k;
                    value |= (uint)(by >> 1) << (7 * n);
                    if ((by & 1) == 0) break;
                }
                if (value > 64)
                {
                    var p = PathOf(value);
                    if (!string.IsNullOrEmpty(p)) refs.Add($"@{start}:{value}:{CleanName(p)}");
                }
            }
            if (refs.Count > 0) guidText += " refs=[" + string.Join(" ", refs) + "]";
        }

        var hex = bits <= (Environment.GetEnvironmentVariable("CHESTCOUNTER_PROBE_HEX") == "1" ? 100000 : 256) ? " hex=" + Convert.ToHexString(data) : "";
        var owner = _channelLabel.GetValueOrDefault(channel, "?");
        Console.Error.WriteLine($"PRB {Now,7:F1}s ch{channel} [{owner}] {CleanName(group)}.{field} bits={bits}{guidText}{hex}{(strings.Count > 0 ? " txt=[" + string.Join(" | ", strings.Where(s => s.Length >= 5)) + "]" : "")}");
    }

    private sealed class StderrLogger : Microsoft.Extensions.Logging.ILogger
    {
        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;
        public bool IsEnabled(Microsoft.Extensions.Logging.LogLevel logLevel) => logLevel >= Microsoft.Extensions.Logging.LogLevel.Information;
        public void Log<TState>(Microsoft.Extensions.Logging.LogLevel logLevel, Microsoft.Extensions.Logging.EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter)
            => Console.Error.WriteLine($"LOG {logLevel} {ReplayAnalyzer.Now,7:F1}s {formatter(state, exception)}");
    }

    protected override void OnNetDeltaRead(uint channelIndex, NetDeltaUpdate update)
    {
        base.OnNetDeltaRead(channelIndex, update);
        if (update.Export is GrantedAbility g && Environment.GetEnvironmentVariable("CHESTCOUNTER_DEBUG") is "1" or "2")
        {
            Console.Error.WriteLine($"DBG ability {Now,7:F1}s ch{channelIndex} idx={update.ElementIndex} del={update.Deleted} ability={CleanName(PathOf(g.Ability))} src={CleanName(PathOf(g.SourceObject))}");
        }
    }

    private static string Safe(Func<string> f) { try { return f(); } catch { return "!"; } }

    internal static float Now => Unreal.Core.ReplayReader<FortniteReplay>.CurrentTime;

    private string PathOf(uint? guid)
    {
        if (guid is null || guid == 0)
        {
            return "";
        }
        return _netGuidCache.TryGetPathName(guid.Value, out var path) ? path : "";
    }

    private static string CleanName(string path)
    {
        var name = path[(path.LastIndexOf('/') + 1)..];
        var dot = name.LastIndexOf('.');
        if (dot >= 0)
        {
            name = name[(dot + 1)..];
        }
        return UaidSuffix.Replace(name, "");
    }

    /// <summary>
    /// The prize handler ability class is sent the first time the recording player receives a treasure:
    /// that gives the exact moment of the first treasure of the match (even one never used).
    /// </summary>
    protected override void OnNetGuidExported(uint netGuid, string pathName)
    {
        if (Environment.GetEnvironmentVariable("CHESTCOUNTER_DEBUG") == "3")
        {
            Console.Error.WriteLine($"EXP {Now,7:F1}s {pathName}");
        }
        if (TrackedExport.IsMatch(pathName))
        {
            var name = CleanName(pathName);
            _firstExport.TryAdd(name, Now);
            // The ability spec references the class default object ("Default__GA_TreasureHandler_..._C").
            if (name.Contains("TreasureHandler", StringComparison.Ordinal))
            {
                _treasureHandlerGuids[netGuid] = name.Replace("Default__", "");
            }
        }
    }

    protected override void OnChannelOpened(uint channelIndex, NetworkGUID? actor)
    {
        base.OnChannelOpened(channelIndex, actor);

        var channel = Channels[channelIndex];
        var archetype = PathOf(channel?.ArchetypeId);
        var actorPath = PathOf(channel?.ActorId);

        var label = CleanName(!string.IsNullOrEmpty(archetype) ? archetype : actorPath);
        if (!string.IsNullOrEmpty(actorPath))
        {
            // Static (level placed) actors have no archetype; their actor path is unique per chest.
            _channelKey[channelIndex] = actorPath;
        }
        else if (channel?.Actor?.Location is { } loc && IsDynamicChest(label))
        {
            // Spawned during the match (buried chest, relic chest): a new GUID each time it becomes relevant again,
            // but always the same spawn location. The location is its identity for the whole match.
            _channelKey[channelIndex] = $"{label}@{loc.X:F0},{loc.Y:F0}";
        }
        else
        {
            _channelKey[channelIndex] = $"guid:{channel?.ActorId}";
        }
        _channelLabel[channelIndex] = label;
        _channelOpenAt[channelIndex] = Now;
        _channelSearched.Remove(channelIndex);
        _lastSearcher.Remove(channelIndex);
        _equipChannelsSeen.Remove(channelIndex);
        _prizeChannelsSeen.Remove(channelIndex);
    }

    /// <summary>Extracts "DelilahPrize:dpid_item4" from the raw property bits (FString inside a struct).</summary>
    private static string? ReadPrizeId(DebuggingObject? raw)
    {
        if (raw == null || raw.TotalBits == 0)
        {
            return null;
        }
        var ascii = new string(raw.Bytes.Select(b => b is >= 32 and < 127 ? (char)b : ' ').ToArray());
        var match = PrizeIdPattern.Match(ascii);
        return match.Success ? match.Value : null;
    }

    protected override void OnExportRead(uint channelIndex, INetFieldExportGroup? exportGroup)
    {
        base.OnExportRead(channelIndex, exportGroup);

        switch (exportGroup)
        {
            case BaseContainer container:
                if (container.SearchingPawn is uint searcher && searcher != 0)
                {
                    _lastSearcher[channelIndex] = searcher;
                }

                var containerKey = _channelKey.GetValueOrDefault(channelIndex, $"ch:{channelIndex}");
                var containerLabel = _channelLabel.GetValueOrDefault(channelIndex, "?");
                // State received together with the channel: the container was searched before it became relevant
                // (coming back near it), or it is a "dormant" box whose channel only opens when it is searched.
                var stale = Now - _channelOpenAt.GetValueOrDefault(channelIndex, float.MinValue) < 0.5f;

                if (container.bAlreadySearched == false)
                {
                    // Refilled during the match (waves every ~3 min): it can be searched, and counted, again.
                    _channelSearched[channelIndex] = false;
                    if (Debug == "1")
                    {
                        Console.Error.WriteLine($"DBG refill {Now,7:F1}s {containerLabel} {containerKey}");
                    }
                }
                else if (container.bAlreadySearched == true && !_channelSearched.GetValueOrDefault(channelIndex))
                {
                    _channelSearched[channelIndex] = true;
                    var openedBy = _lastSearcher.TryGetValue(channelIndex, out var s) ? s : (uint?)null;
                    // Stale + same opener as the last recorded search of that container = that old search seen again,
                    // not a new one. A container without a stable identity (new GUID each time) is recorded once.
                    // Anything else is recorded, even a container already searched: it was refilled and opened again.
                    var seenAgain = stale && (StableKey(containerKey)
                        ? _lastOpener.TryGetValue(containerKey, out var previous) && previous == openedBy
                        : _searched.Contains(containerKey));
                    if (!seenAgain)
                    {
                        _searched.Add(containerKey);
                        _lastOpener[containerKey] = openedBy;
                        _searches.Add(new SearchEvent(Now, containerKey, containerLabel, openedBy, Stale: stale));
                    }
                    else if (Debug == "1")
                    {
                        Console.Error.WriteLine($"DBG seen-again {Now,7:F1}s {containerLabel} {containerKey}");
                    }
                }
                else if (container.bDestroyed && container.SearchingPawn is uint destroyer && destroyer != 0
                    && (_searched.Add(containerKey) || (_channelSearched.TryGetValue(channelIndex, out var wasSearched) && !wasSearched)))
                {
                    // Broken instead of opened (support destroyed, explosion...): the loot spills out and the
                    // server sets SearchingPawn to whoever destroyed it. Counted like an opened chest: once, or once
                    // more if it was refilled since its last opening.
                    _channelSearched[channelIndex] = true;
                    _lastOpener[containerKey] = destroyer;
                    _searches.Add(new SearchEvent(Now, containerKey, containerLabel, destroyer, Destroyed: true, Stale: stale));
                }
                break;

            case GrantedAbility ga when Environment.GetEnvironmentVariable("CHESTCOUNTER_DEBUG") is "1" or "2":
                Console.Error.WriteLine($"DBG abilityexp {Now,7:F1}s ch{channelIndex} owner={ga.OwnerActor} ability={CleanName(PathOf(ga.Ability))} src={CleanName(PathOf(ga.SourceObject))}");
                break;

            case LootCannonFired cannon when cannon.InteractingPawn is uint gunner && gunner != 0:
                var cannonKey = _channelKey.GetValueOrDefault(channelIndex, $"ch:{channelIndex}") + $"#fire{Now:F0}";
                if (_searched.Add(cannonKey))
                {
                    _searches.Add(new SearchEvent(Now, cannonKey, _channelLabel.GetValueOrDefault(channelIndex, "LootCannon"), gunner));
                }
                break;

            case TreasureHuntChest prize:
                var prizeId = ReadPrizeId(prize.PrizeTemplateId);
                if (prizeId != null && prize.bSuccess != false && _prizeChannelsSeen.Add(channelIndex))
                {
                    _prizes.Add(new PrizeEvent(Now, prizeId, prize.OpeningPawn ?? prize.Instigator, PathOf(prize.SourceContainer)));
                }
                break;

            case GenericWeapon weapon:
                var holder = weapon.Instigator ?? weapon.Owner?.Value;
                if (holder is uint h && h != 0 && _equipChannelsSeen.Add(channelIndex))
                {
                    var itemName = weapon.WeaponData?.Name;
                    if (string.IsNullOrEmpty(itemName))
                    {
                        itemName = _channelLabel.GetValueOrDefault(channelIndex, "?");
                    }
                    _equips.Add(new EquipEvent(Now, itemName, h));
                }
                break;

            case FortPickup pickup when pickup.bTossedFromContainer == true && pickup.ItemDefinition != null:
                var item = pickup.ItemDefinition.Name;
                if (string.IsNullOrEmpty(item))
                {
                    item = CleanName(PathOf(pickup.ItemDefinition.Value));
                }
                if (!string.IsNullOrEmpty(item))
                {
                    _tosses.Add(new TossEvent(Now, item));
                }
                break;

            case PlayerPawn pawn when pawn.PlayerState is uint ps && ps != 0:
                var pawnId = Channels[channelIndex]?.ActorId;
                if (pawnId != null)
                {
                    _pawnToPlayerState[pawnId.Value] = ps;
                }
                break;

            case FortPlayerState state:
                var psId = Channels[channelIndex]?.ActorId;
                var playerId = state.PlayerId is null ? state.PlayerID : (int?)state.PlayerId;
                if (psId != null && playerId != null)
                {
                    _playerStateToPlayerId[psId.Value] = playerId.Value;
                }
                break;
        }
    }

    /// <summary>
    /// Parses <paramref name="file"/> and returns what the tracked player did with chests.
    /// </summary>
    /// <param name="username">Epic display name to track. If null/empty or not found, the replay owner is used.</param>
    public MatchRecord Analyze(Stream stream, string file, string? username)
    {
        var replay = ReadReplay(stream);

        if (Environment.GetEnvironmentVariable("CHESTCOUNTER_DEBUG") == "4")
        {
            var groupFilter = new Regex(Environment.GetEnvironmentVariable("CHESTCOUNTER_PROBE_GROUPS") ?? "AbilitySpec", RegexOptions.IgnoreCase);
            foreach (var (path, group) in _netGuidCache.NetFieldExportGroupMap.Where(kv => groupFilter.IsMatch(kv.Key)))
            {
                Console.Error.WriteLine($"GRP {path} ({group.NetFieldExportsLength})");
                for (var i = 0; i < group.NetFieldExports.Length; i++)
                {
                    if (group.NetFieldExports[i] is { } e) Console.Error.WriteLine($"GRP   [{i}] {e.Name}");
                }
            }
        }

        // --- Who are we tracking? -------------------------------------------------------------
        var players = replay.PlayerData?.ToList() ?? new List<PlayerData>();
        PlayerData? target = null;
        var matchedByName = false;

        // One or several Epic names (several accounts), separated by commas. Strict: if names are configured,
        // only those players are counted; a match played by another account is not counted at all.
        var names = (username ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (names.Length > 0)
        {
            target = players.FirstOrDefault(p => names.Any(n => string.Equals(p.PlayerName?.Trim(), n, StringComparison.OrdinalIgnoreCase)));
            matchedByName = target != null;
        }

        uint? recorderPs = replay.GameData?.RecorderId;
        PlayerData? recorderPlayer = null;
        if (recorderPs != null && _playerStateToPlayerId.TryGetValue(recorderPs.Value, out var recorderPlayerId))
        {
            recorderPlayer = players.FirstOrDefault(p => p.Id == recorderPlayerId);
        }
        var notTracked = names.Length > 0 && target == null;
        if (target == null && !notTracked)
        {
            target = recorderPlayer;
        }

        // PlayerState actor(s) of the target -> pawn(s) of the target
        var targetPlayerStates = new HashSet<uint>();
        if (target?.Id != null)
        {
            foreach (var kv in _playerStateToPlayerId.Where(kv => kv.Value == target.Id))
            {
                targetPlayerStates.Add(kv.Key);
            }
        }
        if (targetPlayerStates.Count == 0 && recorderPs != null && !matchedByName && !notTracked)
        {
            targetPlayerStates.Add(recorderPs.Value);
        }

        var isRecorderTarget = recorderPs != null && targetPlayerStates.Contains(recorderPs.Value);
        var targetPawns = _pawnToPlayerState.Where(kv => targetPlayerStates.Contains(kv.Value)).Select(kv => kv.Key).ToHashSet();

        // --- Everything the target searched / interacted with ------------------------------------
        // A stale search of a stable container is real (old searches seen again were dropped, and the target was right
        // there: dormant ammo box, chest broken...). A stale search of a container without identity is not counted.
        var interactions = _searches
            .Where(s => s.SearcherPawn is uint p && targetPawns.Contains(p) && (!s.Stale || StableKey(s.ActorKey)))
            .OrderBy(s => s.Time)
            .Select(s => (s.Time, Kind: ChestKinds.Classify(s.Label), s.Label, s.ActorKey, s.Destroyed))
            .ToList();

        var chests = new List<ChestRecord>();
        var others = new Dictionary<string, int>();
        foreach (var search in interactions)
        {
            if (!ChestKinds.IsChest(search.Kind))
            {
                others[search.Kind] = others.GetValueOrDefault(search.Kind) + 1;
                continue;
            }

            var items = _tosses
                .Where(t => t.Time >= search.Time - 0.25f && t.Time <= search.Time + 0.6f)
                .Select(t => t.Item)
                .ToList();

            chests.Add(new ChestRecord
            {
                TimeSeconds = MathF.Round(search.Time, 1),
                Kind = search.Kind,
                Blueprint = search.Label,
                Destroyed = search.Destroyed,
                Items = items,
                TreasureItems = items.Where(i => TreasureItem.IsMatch(i) && !EventItem.IsMatch(i)).ToList(),
            });
        }

        if (Environment.GetEnvironmentVariable("CHESTCOUNTER_DEBUG") == "1")
        {
            string NameOfPawn(uint pawn) =>
                _pawnToPlayerState.TryGetValue(pawn, out var ps) && _playerStateToPlayerId.TryGetValue(ps, out var pid)
                    ? players.FirstOrDefault(p => p.Id == pid)?.PlayerName ?? $"pid{pid}" : $"pawn{pawn}";
            foreach (var e in _equips)
            {
                Console.Error.WriteLine($"DBG equip {e.Time,7:F1}s {e.Item} by {NameOfPawn(e.Pawn)}{(targetPawns.Contains(e.Pawn) ? " (TARGET)" : "")}");
            }
            foreach (var ex in _firstExport)
            {
                Console.Error.WriteLine($"DBG export {ex.Value,7:F1}s {ex.Key}");
            }
            foreach (var hg in _treasureHandlerGuids)
            {
                Console.Error.WriteLine($"DBG handlerguid {hg.Key} {hg.Value}");
            }
            foreach (var gr in _grants)
            {
                var psName = _playerStateToPlayerId.TryGetValue(gr.PlayerStateActor, out var gpid) ? players.FirstOrDefault(p => p.Id == gpid)?.PlayerName ?? $"pid{gpid}" : $"ps{gr.PlayerStateActor}";
                Console.Error.WriteLine($"DBG grant {gr.Time,7:F1}s {gr.Handler} elem={gr.ElementId} to {psName}{(targetPlayerStates.Contains(gr.PlayerStateActor) ? " (TARGET)" : "")}");
                foreach (var se in _searches.Where(x => x.Time >= gr.Time - 3 && x.Time <= gr.Time + 2).OrderBy(x => Math.Abs(x.Time - gr.Time)).Take(2))
                {
                    Console.Error.WriteLine($"DBG   near {se.Time,7:F1}s {CleanName(se.Label)} by {(se.SearcherPawn is uint sp2 ? NameOfPawn(sp2) : "?")}");
                }
            }
            foreach (var ex in _firstExport.Where(kv => kv.Key.Contains("TreasureHandler")).Take(1))
            {
                foreach (var se in _searches.Where(x => x.Time >= ex.Value - 6 && x.Time <= ex.Value + 3).OrderBy(x => x.Time))
                {
                    Console.Error.WriteLine($"DBG nearhandler {se.Time,7:F1}s (Δ{se.Time - ex.Value:+0.0;-0.0}) {CleanName(se.Label)}{(se.Destroyed ? " DÉTRUIT" : "")} by {(se.SearcherPawn is uint sp ? NameOfPawn(sp) : "?")}{(se.SearcherPawn is uint tp2 && targetPawns.Contains(tp2) ? " (MOI)" : "")}");
                }
            }
            foreach (var pz in _prizes)
            {
                Console.Error.WriteLine($"DBG prize {pz.Time,7:F1}s {pz.PrizeId} by {(pz.Pawn is uint pp ? NameOfPawn(pp) : "?")}{(pz.Pawn is uint tp && targetPawns.Contains(tp) ? " (TARGET)" : "")} chest={CleanName(pz.Container)}");
            }
            foreach (var s in _searches.Where(s => s.Label.Contains("Cannon", StringComparison.OrdinalIgnoreCase)))
            {
                Console.Error.WriteLine($"DBG cannon {s.Time,7:F1}s {s.Label} by {(s.SearcherPawn is uint cp ? NameOfPawn(cp) : "?")}");
            }
            foreach (var i in interactions)
            {
                Console.Error.WriteLine($"DBG interact {i.Time,7:F1}s {i.Kind} {i.Label}{(i.Destroyed ? " (DÉTRUIT)" : "")}");
            }
        }

        // --- Treasures obtained by the target ----------------------------------------------------
        var finds = new List<TreasureFind>();

        // 1) Golden treasure chest: the server spawns an animated chest carrying the prize id and its source container. Certain.
        foreach (var pz in _prizes.Where(p => p.Pawn is uint pawn && targetPawns.Contains(pawn)))
        {
            var label = CleanName(pz.Container);
            var kind = string.IsNullOrEmpty(label) ? "inconnu" : ChestKinds.Classify(label);
            finds.Add(new TreasureFind
            {
                Item = TreasureNames.Friendly(pz.PrizeId),
                RawItem = pz.PrizeId,
                PrizeId = pz.PrizeId,
                FromGoldenChest = true,
                TimeExact = true,
                SourceDestroyed = interactions.Any(i => i.Destroyed && i.ActorKey == pz.Container),
                ObtainedAtSeconds = MathF.Round(pz.Time, 1),
                SourceKind = kind,
                SourceBlueprint = label,
                SourceIsChest = ChestKinds.IsChest(kind),
                SourceCertain = true,
            });
        }

        // Teammates (same team as the target): treasures are shared with the whole team, so the recording player is
        // granted each treasure a teammate finds too. Those are listed apart, never in the player's own count.
        var teammatePawns = new Dictionary<uint, string>();
        if (target?.TeamIndex is int teamIndex)
        {
            foreach (var mate in players.Where(p => p.TeamIndex == teamIndex && p.Id != target.Id))
            {
                var mateStates = _playerStateToPlayerId.Where(kv => kv.Value == mate.Id).Select(kv => kv.Key).ToHashSet();
                foreach (var kv in _pawnToPlayerState.Where(kv => mateStates.Contains(kv.Value)))
                {
                    teammatePawns[kv.Key] = mate.PlayerName ?? "";
                }
            }
        }
        var hasTeam = target?.TeamIndex is int ti && players.Any(p => p.TeamIndex == ti && p.Id != target.Id);
        var teamFinds = new List<TreasureFind>();
        foreach (var pz in _prizes.Where(p => p.Pawn is uint pawn && teammatePawns.ContainsKey(pawn)))
        {
            var label = CleanName(pz.Container);
            var kind = string.IsNullOrEmpty(label) ? "inconnu" : ChestKinds.Classify(label);
            teamFinds.Add(new TreasureFind
            {
                Item = TreasureNames.Friendly(pz.PrizeId),
                RawItem = pz.PrizeId,
                PrizeId = pz.PrizeId,
                FromGoldenChest = true,
                TimeExact = true,
                ObtainedAtSeconds = MathF.Round(pz.Time, 1),
                SourceKind = kind,
                SourceBlueprint = label,
                SourceIsChest = ChestKinds.IsChest(kind),
                SourceCertain = true,
                Teammate = teammatePawns[pz.Pawn!.Value],
            });
        }

        // 2) Items seen in the target's hands are NOT counted on their own (they can come from a teammate,
        //    since treasures are shared by team, or from elsewhere). They are only used to name a treasure below.
        //    Event loot that only looks like a treasure is left out.
        var equippedByMe = _equips.Where(e => targetPawns.Contains(e.Pawn) && !EventItem.IsMatch(e.Item)).OrderBy(e => e.Time).ToList();

        // 3) An item thrown out of a container as a pickup is never a treasure: treasure hunt items go straight into
        //    the inventory and grant a prize handler (4). The only such pickups seen are "happy hour" event extractors
        //    (WID_RelicExtractor_Consumable), plain loot that must not be counted.

        // 4) Prize handlers: every treasure received grants a "GA_TreasureHandler_*" ability on the player state of
        //    the recording player (exact time, one grant per treasure). Counted only if the chest opened/destroyed at
        //    that moment is the target's own (not a teammate's: treasures are shared with the team).
        //    Fallback when no grant could be decoded: first export of the handler class (first treasure only).
        var handlerTimes = _grants
            .Where(g => targetPlayerStates.Contains(g.PlayerStateActor))
            .Select(g => g.Time)
            .OrderBy(t => t)
            .ToList();
        if (handlerTimes.Count == 0 && isRecorderTarget
            && _firstExport.Where(kv => kv.Key.Contains("TreasureHandler")).Select(kv => (float?)kv.Value).FirstOrDefault() is float firstHandler)
        {
            handlerTimes.Add(firstHandler);
        }

        for (var hi = 0; hi < handlerTimes.Count; hi++)
        {
            var h = handlerTimes[hi];
            var nextHandler = hi + 1 < handlerTimes.Count ? handlerTimes[hi + 1] : float.MaxValue;
            // Buried chests grant the prize when the dig completes, ~2.5s before the chest reports "searched": allow
            // up to +4s for them only.
            var closest = _searches
                .Where(x => x.Time >= h - 3 && (x.Time <= h + 2 || (x.Time <= h + 4 && ChestKinds.Classify(x.Label) == ChestKinds.Buried)))
                .OrderBy(x => Math.Abs(x.Time - h))
                .FirstOrDefault();
            var mine = closest != null && closest.SearcherPawn is uint cp && targetPawns.Contains(cp);
            var golden = finds.Concat(teamFinds).FirstOrDefault(f => f.FromGoldenChest && f.ObtainedAtSeconds is float g && Math.Abs(g - h) <= 5);
            if (Environment.GetEnvironmentVariable("CHESTCOUNTER_DEBUG") == "1")
            {
                Console.Error.WriteLine($"DBG decide {h,7:F1}s {(golden != null ? "GOLDEN" : mine ? "MINE" : closest != null ? "OTHER" : "NO-SEARCH")} closest={(closest == null ? "-" : $"{closest.Time:F1}s {closest.Label}")} by={(closest?.SearcherPawn is not uint who ? "?" : targetPawns.Contains(who) ? "me" : teammatePawns.ContainsKey(who) ? "mate" : "enemy")}");
            }

            if (mine && golden == null)
            {
                var kind = ChestKinds.Classify(closest!.Label);

                // Item identity: an item first sent at that exact moment, otherwise the only treasure item the
                // target later had in hand (if there is exactly one kind).
                var item = _firstExport.Where(kv => kv.Key.StartsWith("WID_") && !EventItem.IsMatch(kv.Key) && Math.Abs(kv.Value - h) <= 1).Select(kv => kv.Key).FirstOrDefault();
                if (item == null)
                {
                    var later = equippedByMe.Where(e => e.Time >= h && e.Time < nextHandler).Select(e => TreasureNames.Friendly(e.Item)).Where(n => !n.StartsWith("B_")).Distinct().ToList();
                    if (later.Count == 1)
                    {
                        item = equippedByMe.First(e => e.Time >= h && e.Time < nextHandler && TreasureNames.Friendly(e.Item) == later[0]).Item;
                    }
                }
                var itemName = item != null ? TreasureNames.Friendly(item) : null;

                // No id in the replay: a unique key per treasure (shown as a treasure gizmo).
                var matchStart = replay.GameData?.UtcTimeStartedMatch ?? replay.Info?.Timestamp ?? DateTime.UtcNow;
                var localStart = DateTime.SpecifyKind(matchStart, DateTimeKind.Utc).ToLocalTime();
                var hr = MathF.Round(h, 1);
                var unknownKey = item == null ? $"{TreasureNames.UnknownPrefix}{localStart:yyyy-MM-dd_HH.mm}@{(int)hr / 60}m{(int)hr % 60:00}s" : null;

                finds.Add(new TreasureFind
                {
                    Item = itemName ?? TreasureNames.UnknownGizmoFr,

                    RawItem = item ?? "GA_TreasureHandler",
                    PrizeId = unknownKey,
                    ObtainedAtSeconds = hr,
                    TimeExact = true,
                    SourceKind = kind,
                    SourceBlueprint = closest.Label,
                    SourceIsChest = ChestKinds.IsChest(kind),
                    SourceCertain = true,
                    SourceDestroyed = closest.Destroyed,
                    FromPrizeHandler = true,
                });
            }
            else if (!mine && golden == null && hasTeam && isRecorderTarget)
            {
                // A teammate's treasure: the chest opened at that moment by a teammate, or one out of the recording
                // player's sight (no chest seen at all).
                var byMate = closest?.SearcherPawn is uint sp && teammatePawns.TryGetValue(sp, out var mateName) ? mateName : null;
                var kind = byMate != null ? ChestKinds.Classify(closest!.Label) : "inconnu";
                teamFinds.Add(new TreasureFind
                {
                    Item = TreasureNames.UnknownGizmoFr,
                    RawItem = "GA_TreasureHandler",
                    ObtainedAtSeconds = MathF.Round(h, 1),
                    TimeExact = true,
                    SourceKind = kind,
                    SourceBlueprint = byMate != null ? closest!.Label : "",
                    SourceIsChest = byMate == null || ChestKinds.IsChest(kind),
                    SourceCertain = byMate != null,
                    SourceDestroyed = byMate != null && closest!.Destroyed,
                    FromPrizeHandler = true,
                    Teammate = byMate ?? "",
                });
            }
        }

        if (Environment.GetEnvironmentVariable("CHESTCOUNTER_DEBUG") == "1")
        {
            var enemy = _searches.Count(x => x.SearcherPawn is uint w && !targetPawns.Contains(w) && !teammatePawns.ContainsKey(w) && ChestKinds.IsChest(ChestKinds.Classify(x.Label)));
            var mates = _searches.Count(x => x.SearcherPawn is uint w && teammatePawns.ContainsKey(w) && ChestKinds.IsChest(ChestKinds.Classify(x.Label)));
            var goldenEnemy = _prizes.Count(x => x.Pawn is uint w && !targetPawns.Contains(w) && !teammatePawns.ContainsKey(w));
            Console.Error.WriteLine($"DBG searches enemy={enemy} mates={mates} duration={replay.Info?.LengthInMs / 1000} goldenEnemy={goldenEnemy} team={teammatePawns.Count}");
        }
        teamFinds.Sort((a, b) => (a.ObtainedAtSeconds ?? float.MaxValue).CompareTo(b.ObtainedAtSeconds ?? float.MaxValue));
        finds.Sort((a, b) => (a.ObtainedAtSeconds ?? float.MaxValue).CompareTo(b.ObtainedAtSeconds ?? float.MaxValue));

        var info = replay.Info;
        var header = replay.Header;
        var isRecorder = recorderPs != null && targetPlayerStates.Contains(recorderPs.Value);
        var placement = target?.Placement ?? (isRecorder ? (int?)replay.TeamStats?.Position : null);

        return new MatchRecord
        {
            SessionId = replay.GameData?.GameSessionId ?? $"{info?.Timestamp:O}-{Path.GetFileName(file)}",
            ReplayFile = Path.GetFileName(file),
            AnalyzedAtUtc = DateTime.UtcNow,
            MatchStartUtc = replay.GameData?.UtcTimeStartedMatch ?? info?.Timestamp,
            DurationSeconds = info != null ? (int)(info.LengthInMs / 1000) : null,
            Playlist = replay.GameData?.CurrentPlaylist,
            GameVersion = header?.Branch,
            PlayerName = target?.PlayerName,
            EpicId = target?.EpicId,
            TrackedByUsername = matchedByName,
            NotTrackedOwner = notTracked ? (recorderPlayer?.PlayerName ?? "inconnu") : null,
            Placement = placement,
            TotalPlayers = (int?)replay.TeamStats?.TotalPlayers,
            IsReplayOwner = isRecorder,
            Eliminations = isRecorder ? (int?)replay.Stats?.Eliminations : null,
            ChestsOpened = chests.Count,
            AmmoBoxesOpened = others.GetValueOrDefault(ChestKinds.AmmoBox),
            OtherContainersOpened = others.Where(kv => kv.Key != ChestKinds.AmmoBox).Sum(kv => kv.Value),
            OtherInteractions = others,
            AnalyzerVersion = CurrentVersion,
            IsBattleRoyale = !string.IsNullOrEmpty(replay.GameData?.CurrentPlaylist) || chests.Count > 0,
            Chests = chests,
            TreasureFinds = finds,
            TeamTreasureFinds = teamFinds,
        };
    }
}

internal static class SafeExt { }

public static class TreasureNames
{
    /// <summary>Key prefix of a treasure whose prize id is not in the replay ("Inconnu:" before version 1.1).</summary>
    public const string UnknownPrefix = "Unknown:";
    public const string UnknownGizmoFr = "Gizmo de trésor", UnknownGizmoEn = "Treasure gizmo", UnknownGizmoEs = "Artilugio de tesoro";
    private const string OldUnknownPrefix = "Inconnu:";

    /// <summary>Prize ids whose reward is known, in the chosen language (built in: nothing to fill in by hand).</summary>
    private static string? KnownPrize(string id) => id.ToLowerInvariant() switch
    {
        "delilahprize:dpid_item1" => L.T("Gantelets d'A$AP Rocky en or massif (pioche) ?", "A$AP Rocky solid gold gauntlets (pickaxe) ?", "Guanteletes de oro macizo de A$AP Rocky (pico) ?"),
        "delilahprize:dpid_item2" => L.T("Plunder in Paradise (écran de chargement)", "Plunder in Paradise (loading screen)", "Plunder in Paradise (pantalla de carga)"),
        "delilahprize:dpid_item3" => L.T("Mercedes-AMG G 63 (véhicule) ?", "Mercedes-AMG G 63 (vehicle) ?", "Mercedes-AMG G 63 (vehículo) ?"),
        "delilahprize:dpid_item4" => L.T("No Trespassing (piste musicale)", "No Trespassing (music track)", "No Trespassing (pista musical)"),
        _ => null,
    };

    /// <summary>Display name of a prize id, or of a treasure item blueprint.</summary>
    public static string Friendly(string item)
    {
        if (item.Contains(':'))
        {
            if (KnownPrize(item) is { } known)
            {
                return known;
            }
            if (item.StartsWith(UnknownPrefix, StringComparison.OrdinalIgnoreCase) || item.StartsWith(OldUnknownPrefix, StringComparison.OrdinalIgnoreCase))
            {
                // Not a golden chest prize (those carry their id): one of the treasure hunt gizmos (golden
                // firework, portable extractor, locators, extraction accelerator), exact one not in the replay.
                return L.T(UnknownGizmoFr, UnknownGizmoEn, UnknownGizmoEs);
            }
            // A prize whose reward is not known yet: its id means nothing to the player.
            return L.T("Récompense de coffre doré", "Golden chest reward", "Recompensa de cofre dorado");
        }

        var i = item.ToLowerInvariant();
        if (i.Contains("flameceleb")) return "Feu d'artifice doré";
        if (i.Contains("relicextractor")) return "Extracteur portable";
        if (i.Contains("relicfinder")) return "Localisateur d'esprit";
        if (i.Contains("luckylocator")) return "Localisateur chanceux";
        if (i.Contains("smugglerextractionkey") || i.Contains("extractionaccelerat")) return "Accélérateur d'extraction";
        return item;
    }
}

public static class ChestKinds
{
    // Chests (counted in the total)
    public const string Normal = "Coffre";
    public const string Rare = "Coffre rare";
    public const string Buried = "Coffre enterré";
    /// <summary>Rare sprite chest, behind the locked vault doors (needs a key): B_TieredChest_Relics_C and its variants.</summary>
    public const string Extraction = "Coffre d'esprit (rare)";
    public const string PosterChest = "Coffre des affiches d'esprit";
    public const string Other = "Autre coffre";

    // Other interactions (tracked separately, not in the chest total)
    public const string Cannon = "Canon à butin";
    public const string AmmoBox = "Caisse de munitions";
    public const string FoodBox = "Caisse de nourriture";
    public const string OtherContainer = "Autre conteneur";

    private static readonly HashSet<string> ChestSet = new() { Normal, Rare, Buried, Extraction, PosterChest, Other };

    public static bool IsChest(string kind) => ChestSet.Contains(Canonical(kind));

    /// <summary>Kind names stored by older versions, mapped to today's name (history is kept as it was saved).</summary>
    public static string Canonical(string kind) => kind is "Coffre d'extraction / reliques" or "Coffre de chambre forte" ? Extraction : kind;

    public static string Classify(string blueprint)
    {
        var b = blueprint.ToLowerInvariant();
        if (b.Contains("lootcannon") || b.Contains("cannon")) return Cannon;
        if (b.Contains("ammo")) return AmmoBox;
        if (b.Contains("foodbox") || b.Contains("food_box")) return FoodBox;
        if (b.Contains("rarechest") || b.Contains("legendary") || b.Contains("rare_chest")) return Rare;
        if (b.Contains("buried")) return Buried;
        if (b.Contains("spriteposter") || b.Contains("booty")) return PosterChest;
        if (b.Contains("relic") || b.Contains("extraction")) return Extraction;
        if (b.Contains("tiered_chest") || b.Contains("normalchest") || b.Contains("standardchest")) return Normal;
        // Coolers, barrels, etc. are not chests.
        return b.Contains("chest") ? Other : OtherContainer;
    }
}
