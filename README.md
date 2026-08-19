# kicad-template-project

## Automatic setup
Run once after creating a new repository from this template:
```sh
python3 init.py        # Windows: python init.py
```
It asks for the repository name, the PBS number and a board title, renames `template.kicad_*` accordingly, fills the title block (`#122-000 Board Title`, date), runs `setup-libs.py` (see below) and deletes itself. It then offers to fold everything into the single commit GitHub created from the template, reworded to `feat: :tada: initialise project <name>` – review with `git show --stat` and `git push --force-with-lease`. Decline (or `--no-amend`) and it only stages, so you commit yourself. `python3 init.py --help` lists the flags that pre-answer the prompts.

## Libraries
This project uses [`kicad-custom-libs`](https://github.com/LightYourWay/kicad-custom-libs) through the KiCad path variable `LRV_CUSTOM_LIBS`. That variable is a per-machine KiCad setting, so every machine that opens the project needs it once:
```sh
python3 setup-libs.py  # Windows: python setup-libs.py
```
It checks whether the library is cloned and `LRV_CUSTOM_LIBS` is set in KiCad, and clones / sets it if not (close KiCad first, it rewrites its settings on exit). By hand instead:
1. Clone [`kicad-custom-libs`](https://github.com/LightYourWay/kicad-custom-libs)
2. In KiCad, open **Preferences** &rarr; **Configure Paths** and add `LRV_CUSTOM_LIBS`, pointing it to the cloned repo path
3. You’ll now have access to the custom symbols, footprints, and 3D models included with the custom library. Enjoy! ✨
