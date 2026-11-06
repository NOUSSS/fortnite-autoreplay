# Templates

Un template est une **petite capture nette** du bouton à reconnaître, sans texte
décoratif inutile et sans zone qui varie (compteur, pseudo, curseur).

## Capturer

1. Mets Fortnite au premier plan, sur l'écran concerné.
2. Lance :

   ```powershell
   python -m fortnite_auto_replay.tools.capture_templates --state replay
   ```

3. Place la souris **au centre du bouton**, appuie sur `F7`.
   `F8` passe à l'état suivant (`continue`, `ready`, `pass_battle`), `F9` quitte.

Les fichiers vont dans `templates/<état>/<état>_NN.png`.

## Règles

- Zone d'environ `320x90` : assez large pour être discriminante, assez petite
  pour ne pas inclure le fond animé. Ajuste avec `--size`.
- 2 à 4 variantes par état suffisent : pleine résolution et, si besoin, une
  résolution différente (`vision.scales` gère déjà les petites variations).
- Évite les zones qui changent : chrono, nombre de joueurs, barre de progression.
- Vérifie après chaque capture avec `fnreplay check --dry-run`.

## Valider

```powershell
python -m fortnite_auto_replay check --dry-run
```

Sortie = JSON des meilleurs scores par template + une image annotée dans
`captures/check-*.png`.

- Score ≥ 0.85 : bon template.
- Score 0.82–0.85 : limite, le seuil sera fragile.
- Score < 0.82 sur l'écran réel : recadre mieux, ou baisse `vision.threshold`
  en dernier recours.

Un template quasi uniforme (fond uni) est ignoré au chargement, avec un
avertissement : recadre sur les éléments textuels/colorés du bouton.

## États livrés

| État | Bouton visé |
| --- | --- |
| `pass_battle` | « Passer » (écran de fin de partie / récompenses) |
| `replay` | « Rejouer » |
| `continue` | « Continuer » |
| `ready` | « Prêt » (salon) |

Ajouter un état : entrée dans `config/default.json` (`slug`, `label`,
`priority`) puis nouveau dossier `templates/<slug>/`.
