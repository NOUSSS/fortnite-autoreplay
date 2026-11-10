# Lecteur de replays

`replay-reader.exe` lit les replays de Fortnite et rend, pour chacun, les
coffres ouverts ou cassés par le joueur qui l'a enregistré : une ligne JSON par
fichier donné en argument.

```
replay-reader.exe <fichier.replay>…
replay-reader.exe --version        # version de l'analyse (relit les replays si elle change)
```

```json
{"file":"a.replay","ok":true,"session_id":"…","start":"2024-11-03T23:28:32.794Z","duration_s":900,"playlist":"…","battle_royale":true,"chests":5,"destroyed":2,"ammo_boxes":8,"analyzer":15}
{"file":"b.replay","ok":false,"error":"InvalidReplayException: Invalid replay file"}
```

`chests.py` l'appelle un replay à la fois, sans fenêtre et en priorité basse.

## Construire

SDK .NET 10, puis depuis la racine du dépôt :

```
python packaging/build_reader.py
```

L'exécutable autonome (.NET embarqué, environ 38 Mo) est posé dans
`replay-reader/replay-reader.exe`, où l'application le cherche depuis les
sources. `packaging/build.py` le construit aussi dans l'application à
distribuer.

## Code repris

`vendor/` est repris sans modification de
[LED-N/Fortnite-ChestCounter](https://github.com/LED-N/Fortnite-ChestCounter)
(licence MIT, `vendor/LICENSE-ChestCounter`), commit `1f387987` (version 1.4.0,
analyse 15) :

- `vendor/ChestCounter/` : `ReplayAnalyzer.cs`, `Models.cs` et `Lang.cs` de
  `src/ChestCounter` ;
- les autres dossiers : `lib/` en entier, soit FortniteReplayDecompressor
  (licence MIT, `vendor/LICENSE-FortniteReplayDecompressor`) avec les
  changements de ChestCounter décrits dans `vendor/PATCHES.md`.

`Directory.Build.props` vient aussi de ChestCounter ; seuls `Program.cs` et
`ReplayReader.csproj` sont propres à ce dépôt.

## Mettre à jour

Quand Epic change le format des replays, ChestCounter publie un correctif. Pour
le reprendre : recopier les mêmes fichiers depuis son dépôt, noter le nouveau
commit ci-dessus, reconstruire, puis lire quelques replays récents. Si la
version de l'analyse (`ReplayAnalyzer.CurrentVersion`) a changé, l'application
relit d'elle-même les replays encore présents.
