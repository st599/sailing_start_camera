# Flags

The video shows the flags that are flying in a vertical stack, below the countdown. Each has
a label saying what it means.

## Standard flags

These are built in, and can be used by name in any club's configuration:

- Letter flags `A` to `Z`
- Number pennants `0` to `9`
- `AP`, `First Substitute` and `black`

The built-in flags are drawn by this project's code, so they come under its MIT licence. They
are simplified drawings for a small display, so their exact colours and proportions won't
match a real flag.

## Setting up a club's class flags

Give each class its flag in the configuration, using the flag's name:

```json
{ "name": "Merlin Rocket", "class_flag": "O" }
```

Its label then reads `O Merlin Rocket`.

## Custom flags

To add a flag, such as a class symbol, or to replace a built-in flag, put an image in the
`custom_flags` folder and name the file after the flag:

```text
custom_flags/Solo.jpg
```

```json
{ "name": "Solo", "class_flag": "Solo" }
```

- PNG, JPEG, WebP and BMP images work, and a PNG can have a transparent background.
- The file name's capitals don't matter.
- The folder isn't in Git, as class symbols may belong to their class associations. Copy it
  to each computer with `config.json` and `secrets.json`.

If a flag has no image and isn't a standard flag, it appears as a white panel with its name,
and a warning is logged, so a signal can't silently disappear.

## Flags that fly all day

List these in `sequences.day_flags`. They show whatever the sequences are doing, including
during a postponement or abandonment:

```json
{ "sequences": { "day_flags": ["Y"] } }
```

A race day where flag Y is in force would use this. Where sailing instructions require
lifejackets by a written rule instead of by flag Y, leave it out.
