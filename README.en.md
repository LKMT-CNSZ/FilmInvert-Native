# FilmInvert Native

**A local film inversion and photo editing tool for Windows.**

FilmInvert Native turns digitized color and black-and-white negatives into editable positive images. It brings RAW input, whole-strip scans, independent photo editing, batch proofing and ICC-tagged 16-bit TIFF export into one native Qt desktop application. Installer and portable ZIP editions include their own Python runtime.

Version **3.0.1** adds a **Simplified Chinese / English** interface. Choose a language from the toolbar or **View → Language / 语言**. The interface updates immediately and remembers your choice. Open photos, edits, history and queued exports remain in place; filenames, roll tags and other user-entered text keep their original spelling.

## Main features

- **Negative inversion:** color-negative, black-and-white-negative and positive modes; automatic film-base estimation or manual sampling; density range, contrast and film matrix presets.
- **Tone and color:** exposure, white balance, temperature, tint, RGB gains, a shared tone curve, eight-color HSL and saturation.
- **Single images and film strips:** automatic or manual frame layouts, independent frame parameters, frame ordering and whole-film export.
- **Geometry:** free or fixed-ratio crop, 90° rotation, fine straightening with multiple reference lines, film-edge suggestions and four-corner perspective correction.
- **Local editing:** clone repair, exposure brushes and linear gradients with radius, feather and strength controls. Local edits belong to each photo or frame.
- **Illumination and repair:** reference flat fields, strip-gap illumination estimation, vignette compensation, isolated dust repair and estimated underexposed-channel repair.
- **Color management:** linear sRGB and ProPhoto working spaces; sRGB, Adobe RGB (1998) and ProPhoto RGB TIFF output with matching ICC profiles; supported embedded input ICC profiles, display RGB ICC profiles and output gamut preview.
- **Chart calibration:** reference CSV import, individual or grid sampling, linear RGB matrix fitting, training and validation patch roles, and reusable correction profiles.
- **Photo library:** independent edits, ratings, pick / reject flags, roll tags, search, filters, source relinking, backup and restoration.
- **Batch workflow:** per-photo preparation, grouping, selected-parameter synchronization, independent EV adjustments, 4-up / 9-up proofing, visual review and an export queue with frozen parameter snapshots.
- **Delivery:** 16-bit TIFF, parameter recipes, film projects, PNG contact sheets, batch summaries and project transfer packages that can include source files.

## Download

Download the Windows installer or portable ZIP from [Releases v3.0.1](https://github.com/LKMT-CNSZ/FilmInvert-Native/releases/tag/v3.0.1). Matching source and SHA256 checksums are provided on the same page.

## Get started

1. Install the Windows x64 setup, or fully extract the portable ZIP. No separate Python installation is needed.
2. For the portable edition, run `Launch FilmInvert.cmd` or `启动胶片工作室.cmd` from the extracted folder.
3. Choose **English** in the toolbar. Open a photo with **Ctrl+O**, or drop multiple photos to populate the bottom navigator.
4. Choose an input mode and negative type. Check the film base, white balance and exposure before refining curves and color.
5. Use **Film edges / frames** for whole-strip scans; crop, straighten or correct perspective for individual frames.
6. Check important details at **100% view** and export an ICC-tagged **16-bit TIFF**.

For larger sets, open **Batch workflow** with **Ctrl+Shift+B**. Prepare photos individually, group compatible sources, synchronize only the intended parameters, refresh proofs and review before queueing exports. Queue snapshots retain the parameters that were current when each job was added.

## Useful shortcuts

| Shortcut | Action |
| --- | --- |
| Ctrl+O | Open an image |
| Ctrl+I | Open photo library / export queue |
| Ctrl+Shift+B | Open batch workflow |
| Ctrl+R | Crop |
| Ctrl+J | Local editing |
| Ctrl+K | Chart calibration |
| Ctrl+Shift+R | Input frames / film-edge alignment |
| Ctrl+Z / Ctrl+Shift+Z | Undo / redo |
| F / Ctrl+1 | Fit to window / 100% view |
| ← / → | Previous / next photo or frame |
| 0–5 / P / X / U | Rating / pick / reject / clear flag |
| Shift+R | Mark reviewed |
| Ctrl+Shift+S | Export 16-bit TIFF |

Navigation and rating shortcuts pause while you are editing text, numeric fields or combo boxes.

## Files and data

Installed editions use `%LOCALAPPDATA%\FilmInvertNative\data` for settings, sessions and the library, and `%LOCALAPPDATA%\FilmInvertNative\exports` as the default export folder. Writable portable editions use `data` and `out` beside the program; when those locations are not writable, they use the user folders above. **Help → Open user data folder** shows the actual location. Choose a different export folder on the Export tab.

Projects and library backups retain edits and source references. Keep the original images available. Use a project transfer package when the originals also need to travel with a project. Back up existing data before moving or combining installations.

Frozen export jobs from an older version are checked against their original engine signature. If a job is rejected after upgrading, review its edits and add a new job in the new version.

## Format and processing notes

TIFF, PNG, JPEG and RAW input are available; RAW support depends on the bundled decoder and the particular camera/file variant. Supported embedded input ICC conversion uses RGB matrix/TRC profiles. Some ICC profiles require conversion in another color-managed application first.

Automatic inversion is a starting point. Preview sampling and reduced-resolution dust detection can differ from a full-resolution export. Clone repair uses pixels from the image; dust and underexposed-channel repair are estimates. Chart error reports describe the sampled data and do not certify a device's color accuracy. Keep originals and check important results at full resolution.

The Chinese and English interface covers application menus, controls and dialogs. User content stays unchanged; low-level decoder errors, diagnostic tracebacks and existing report contents can retain their original language.

Processing runs locally. The native desktop application does not start a web server or upload photographs for processing. See `THIRD_PARTY` for bundled component notices and licenses.

## Short description for GitHub

A local film inversion and photo editing tool for Windows, with Chinese and English interfaces. Supports color and B&W negatives, RAW and whole-strip scans, independent photo editing, batch proofing and export, and ICC-tagged 16-bit TIFF output. Available as an installer or portable ZIP.
