# Fujifilm film-simulation profiles for LightCraft

Selectable "Camera Matching" profiles for [LightCraft](https://getartcraft.com/apps/lightcraft), in the spirit of
Lightroom's Fujifilm camera profiles: Velvia, ASTIA, CLASSIC CHROME, PRO Neg Hi / Std, ACROS and MONOCHROME
(each plain and with Ye / R / G filters) and SEPIA.

They are for **X-Trans III** bodies (X-T2, X-Pro2, X-T20, X-E3, X-H1, X100F) and were checked on X-T2 raws.

## How they work

LightCraft gives a Fujifilm raw its starting colour by matching it to the camera JPEG embedded in the RAF. For a
photo shot in PROVIA/STANDARD, the starting look therefore already is PROVIA. Each profile here is a 33-point 3D
LUT (`.cube`) that maps *PROVIA-rendered* colour to another simulation, so choosing it re-renders the photo as if
that simulation had been set in the camera.

Consequences:

- **Shoot (or have shot) the raw in PROVIA/STANDARD.** On a raw shot in another simulation the profile stacks on
  top of that simulation.
- `Camera PROVIA/STANDARD` is an identity LUT, included so the set is complete. LightCraft's built-in *Color*
  profile gives the same picture.
- ACROS has no grain: the camera adds it per ISO. Use LightCraft's Grain controls.
- LightCraft renders photos with a LUT profile on the CPU.
- The Amount slider (0-200 %) works as with the built-in looks.

## Download

The 15 ready-made profiles are in the [`Fujifilm Camera Matching`](Fujifilm%20Camera%20Matching) folder. Download
the repository (Code → Download ZIP) and import that folder as described under *Installing in LightCraft*.

## Where the colour comes from

The colour data comes from Stuart Sowerby's
[Fuji Film Simulation Profiles](https://blog.sowerby.me/fuji-film-simulation-profiles/), which he measured from
Fujifilm's own RAW FILE CONVERTER. The `.cube` files here are derived from his tables. He publishes them free of
charge but states no licence, and we found no way to contact him, so they are shared here with credit and without
his explicit permission. **Stuart, if you would like them changed or removed, please open an issue and they will
be.** The MIT licence in this repository covers only the conversion script and documentation.

## Building the profiles yourself

You only need this to reproduce or change the conversion.

1. Download `Fujifilm-XTrans-III.zip` (HaldCLUT, sRGB) from the page above and unzip it.
2. Install Python 3 and Pillow (`pip install pillow`).
3. Run:

   ```
   python build_luts.py "path/to/Fujifilm XTrans III" "Fujifilm Camera Matching"
   ```

   It takes about a minute and writes 15 `.cube` files, printing the fit error of each (around 1 level in 255 on
   average).

## Installing in LightCraft

Import the `Fujifilm Camera Matching` folder as profiles (the folder name becomes the group in the profile
browser). From the command line, with LightCraft closed, put the request in a file, e.g. `import.jsonl` (on
Windows, double the backslashes in the path):

```
{"command":"profile.import","params":{"paths":["C:\\path\\to\\Fujifilm Camera Matching"]}}
```

and run it against your library:

```
lightcraft-cli run --library "path/to/LightCraft Library" --script import.jsonl
```

To remove them, delete the profiles in the profile browser.

## How the conversion works

Each source table maps a neutral raw render `x` to a simulation `S(x)`. The profile needed is `S ∘ P⁻¹`, where `P`
is the PROVIA table. Instead of inverting `P`, every table entry is treated as a pair `(P(x), S(x))` and an affine
function is fitted to the pairs around each node of the output grid (local linear regression). Colours PROVIA
never produces are filled by extending the neighbouring colour shift smoothly; for the black-and-white looks that
region is kept a function of brightness.

## Credits

- Film-simulation measurements: Stuart Sowerby, <https://blog.sowerby.me/fuji-film-simulation-profiles/>.
- FUJIFILM, PROVIA, Velvia, ASTIA, CLASSIC CHROME and ACROS are trademarks of FUJIFILM Corporation. This project
  is not affiliated with or endorsed by FUJIFILM, Adobe or the LightCraft developers.
