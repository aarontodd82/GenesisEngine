# Test VGM Files

Place your VGM and VGZ files here for testing.

## Converting to C Headers

From this directory, run:

```bash
python ../vgm2header.py yourfile.vgm
```

Or convert all files at once:

```bash
python ../vgm2header.py *.vgm *.vgz
```

The generated `.h` files can be copied to your Arduino sketch folder.

## Where to Find VGM Files

- **Project2612**: https://project2612.org/ (Genesis/Mega Drive)
- **SMS Power**: https://www.smspower.org/Music/VGMs (Master System, Game Gear)
- **VGMRips**: https://vgmrips.net/ (Various systems)

## Supported Formats

| Extension | Description |
|-----------|-------------|
| .vgm | Uncompressed VGM |
| .vgz | Gzip-compressed VGM |

Both formats are supported by the converter tool.
