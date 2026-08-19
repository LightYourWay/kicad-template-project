# kicad-template-project

## Automatic setup
Run once after creating a new repository from this template:
```sh
python3 init.py        # Windows: python init.py
```
It asks for the repository name, the PBS number and a board title, renames `template.kicad_*` accordingly, fills the title block (`#122-000 Board Title`, date), then deletes itself. It also does the manual setup below for the machine it runs on: it makes sure [`kicad-custom-libs`](https://github.com/LightYourWay/kicad-custom-libs) is cloned and `LRV_CUSTOM_LIBS` is set in KiCad (clones and sets it if not). Review with `git status` and commit. `python3 init.py --help` lists the flags that pre-answer the prompts.

## Manual setup
Needed once on every machine that works on this project:
1. Clone the [`kicad-custom-libs`](https://github.com/LightYourWay/kicad-custom-libs) repository
2. In KiCad, open **Preferences** &rarr; **Configure Paths** and add `LRV_CUSTOM_LIBS`, pointing it to the cloned repo path
3. You’ll now have access to the custom symbols, footprints, and 3D models included with the custom library. Enjoy! ✨
