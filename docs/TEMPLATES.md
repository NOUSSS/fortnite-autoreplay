# Templates

Un template est une **petite capture nette** du bouton à reconnaître, sans texte
décoratif inutile et sans zone qui varie (compteur, pseudo, curseur).

## Capturer

1. Mets Fortnite au premier plan, sur l'écran concerné.
2. Lance :

   ```powershell
   python -m fortnite_auto_replay.tools.capture_templates --state replay
   ```

3. Place la souris **au centre du bouton**, appuie sur `F7`, puis écarte la
   souris : la capture a lieu 1,5 s plus tard (`--delay`), sans curseur ni
   effet de survol, comme le bot voit le bouton avant de cliquer.
   `F8` passe à l'état suivant (`continue`, `ready`, `pass_battle`), `F9` quitte.

Les fichiers vont dans `templates/<état>/<état>_NN.png`, sans jamais écraser un
numéro existant. La capture passe par la même chaîne que le bot (backend,
zone client du jeu).

### Zone attendue

Quand la fenêtre du jeu est trouvée, l'outil écrit à côté du PNG un
`<état>_NN.json` :

```json
{"window": [1920, 1080], "box": [1500, 950, 320, 90]}
```

`window` = taille de la zone client du jeu, `box` = position du template dans
cette zone. Le bot s'en sert pour :

- **mettre le template à l'échelle** d'une autre résolution (rapport des
  hauteurs de fenêtre) ;
- **ne chercher le bouton que dans sa zone** (`box` ± `vision.zone_margin` de
  la frame), avec un balayage complet toutes les `vision.full_scan_interval_s`
  secondes au cas où l'interface aurait bougé.

Un PNG sans JSON reste utilisable : il est cherché partout, à l'échelle de
capture. Un JSON illisible est ignoré avec un avertissement.

## Règles

- Zone d'environ `320x90` : assez large pour être discriminante, assez petite
  pour ne pas inclure le fond animé. Ajuste avec `--size`.
- 2 à 4 variantes par état suffisent. Une autre résolution n'en demande
  pas de nouvelle si le JSON est présent ; `vision.scales` couvre les petites
  variations restantes.
- Évite les zones qui changent : chrono, nombre de joueurs, barre de progression.
- Vérifie après chaque capture avec `fnreplay check --dry-run`.

## Valider

```powershell
python -m fortnite_auto_replay check --dry-run
```

Sortie = JSON des meilleurs scores par template (`pos` = position à l'écran,
`in_zone` = trouvé dans sa zone attendue) + une image annotée dans
`captures/check-*.png`. `check` cherche partout, zones comprises : un
`in_zone: false` sur le vrai bouton veut dire que le JSON ne correspond plus.

- Score ≥ 0.85 : bon template.
- Score 0.82–0.85 : limite, le seuil sera fragile.
- Score < 0.82 sur l'écran réel : recadre mieux, ou baisse `vision.threshold`
  en dernier recours.

`check` capture la zone client de Fortnite si sa fenêtre est trouvée (le jeu
doit alors être au premier plan pour que l'image soit la bonne), sinon le
moniteur entier.

## Pendant les parties

Quand un score tombe dans `[threshold - engine.doubt_margin, threshold[`, le
bot sauve une frame annotée `captures/doubt-<état>-*.jpg` (au plus une par état
et par `doubt_interval_s`, `doubt_max_snapshots` par session). Plusieurs
frames douteuses sur un vrai bouton : recadre ce template ou ajoute-en une
variante. Sur un faux bouton : le seuil protège, ne le baisse pas.

Un template quasi uniforme (fond uni) est ignoré au chargement, avec un
avertissement : recadre sur les éléments textuels/colorés du bouton.

## États livrés

| État | Bouton visé |
| --- | --- |
| `pass_battle` | « Passer » (écran de fin de partie / récompenses) |
| `back_to_lobby` | « Retourner au salon » (fin de partie) |
| `replay` | « Rejouer », désactivé : le bot retourne au salon puis clique sur « Prêt » |
| `continue` | « Continuer » |
| `ready` | « Prêt » (salon) |
| `still_here_ready` | « Prêt » du dialogue « Vous êtes toujours là ? » |
| `still_here_not_yet` | « Pas encore » du même dialogue |

Les deux templates du dialogue viennent d'une capture d'écran du jeu en
1920x1080 (recadrée au pixel, coordonnées paires pour l'analyse à
demi-échelle). Sur cette capture, chacun score 1,00 sur son bouton et au plus
0,69 sur l'écran de fin de partie et le salon (seuil 0,82). Si leurs scores baissent (nouvelle
saison, autre résolution), les recapturer avec `capture_templates`.

Le template de « Retourner au salon » vient de quatre frames de fin de partie
sauvées par le bot (`captures/doubt-replay-*.jpg`, analyse à demi-échelle) :
moyenne des quatre, recadrée sur le texte, au-dessus de l'annotation dessinée
sous le bouton, puis agrandie x2 au plus proche voisin pour rejoindre le
1920x1080 des autres templates : à `capture.scale` 0,5, la réduction redonne
exactement le découpage. Chaque frame le retrouve à 1,00 (avec
les trois autres seulement), au plus 0,70 ailleurs sur le même écran
(« S'équiper d'un esprit ») et 0,45 en partie. Une capture nette avec
`capture_templates --state back_to_lobby` reste préférable.

Pour revenir à « Rejouer » : `enabled` à `true` sur `replay` et à `false` sur
`back_to_lobby`, dans `config/default.json` (ou dans une copie complète de la
liste `states` dans `config/local.json`, qui la remplace entière).

Ajouter un état : entrée dans `config/default.json` (`slug`, `label`,
`priority`) puis nouveau dossier `templates/<slug>/`.
