# Changes made to FortniteReplayDecompressor

Original code: https://github.com/Shiqan/FortniteReplayDecompressor (MIT license, see LICENSE-FortniteReplayDecompressor), commit 76f74c1.

Changes for ChestCounter:

1. `Unreal.Core/ReplayReader.cs`: static `CurrentTime` field (timestamp of the frame being read).
2. `Unreal.Core/NetFieldParser.cs`: `AliasResolver`, which maps an unknown group (new chest blueprint) to an already known type.
3. `Unreal.Core/Models/NetGuidCache.cs`: `KeepOnCleanup` option (debugging).
4. `FortniteReplayReader/FortniteReplayReader.cs`: an unknown event no longer throws in Debug mode.
5. `FortniteReplayReader/Models/NetFieldExports/Items/BaseContainer.cs`: new `SearchingPawn` property (who searched the chest).
6. `GenericContainer.cs` and `Weapons/GenericWeapon.cs`: generic types (containers and equipped items: treasure hunt items).
7. Simplified `.csproj` files; `-p:OfflineBuild=true` replaces the logging NuGet package with the shared framework (build without NuGet access).
8. `Unreal.Core/RawProbe.cs` + `ReplayReader.cs`: `RawProbe` hook (no-op by default). It receives the raw data the parser does not decode: properties and ClassNetCache fields of a group that is not read (`ProbeReplicatorBunch`), unknown or unnamed fields of a read group (`ProbeField`), sub-objects without a group ("Failed group"). ChestCounter uses it to read the `GA_TreasureHandler_*` grants in the `ActivatableAbilities` FastArray of the PlayerState (fields without exported names), and for `CHESTCOUNTER_DEBUG=4` debugging.
9. `Unreal.Core/BitReader.cs`: `SetTempEnd` always saves the current end, even on overflow. Otherwise the next `RestoreTempEnd` restored a stale end, reading went backwards and the parser looped until the 3-minute timeout ("analysis took too long").
10. `Unreal.Core/ReplayReader.cs` + `BinaryReader.cs`: reading a replay **still being written**. `ReadReplayChunks` stops at the last complete chunk (`IsPartialReplay`, `BinaryReader.Length`), and an encrypted replay flagged "live" is accepted when its key is present.
11. `Unreal.Core/ReplayReader.cs`: in a "live" replay, an event or data chunk that cannot be decrypted is skipped, or stops the reading, instead of failing the analysis. Note: during the match, Fortnite leaves the encryption key at zero in the header and only writes the real key at the end. The data of a match in progress therefore cannot be read (`ReplayHeaderProbe.KeyMissing`).
12. `Unreal.Core/Models/NetGuidCache.cs`: level placed actors whose instance name is not their class name (`Tiered_Chest_Athena47_UAID_...`, `Tiered_Chest_Athena5_6e02a0a859c897db_11`) are matched to the exact `<name>_C` class after stripping the UAID / instance hash and the number suffix. Before, these chests were never decoded ("Failed group") and were not counted.
13. `Unreal.Core/ReplayReader.cs`: a ClassNetCache custom delta property that `RawProbe` asks for (`FortAbilitySystemComponentAthena_ClassNetCache.ActivatableAbilities`) is handed over raw instead of being decoded as `GrantedAbility`, whose generic NetDelta decoding gives garbage indices. Without it the treasure handler grants were never read on players with the `FortAbilitySystemComponentAthena` class.
14. `FortniteReplayReader/Models/NetFieldExports/Items/BaseContainer.cs`: `bAlreadySearched` is `bool?` and `SearchAnimationCount` is `uint?`, so an update that does not carry them (null) is no longer read as "not searched" / 0. ChestCounter uses the explicit `false` to see that a chest was refilled and can be opened (and counted) again.
15. `Unreal.Core/Models/NetGuidCache.cs`: a path that found no export group was remembered as failed for the whole replay. An actor sent a moment before its class's export group (e.g. a rare chest in view before the first rare chest is fully sent) was then never decoded, and its later updates (opened...) were lost. Adding a new export group clears that list so those paths are tried again.
