<p align="center">
  <img src="https://github.com/user-attachments/assets/e86f7b7a-e971-4c8e-b6ea-b344ae24563e" alt="Github Cover" width="450">
</p>


# DuoPrincess - English Translation Patch

This is a full English translation for [EasyGameStation](https://egs-soft.info/)'s 2002 [Threads of Fate](https://en.wikipedia.org/wiki/Threads_of_Fate) (DewPrism) doujin game, [DuoPrincess](https://egs-soft.info/product/duoprincess/duopri.html).

Over a decade ago, RadicalR and co researched this game and made tools to extract and repack files. However, a planned translation effort was abandoned and over time, the source code for tools needed to edit the game's files were lost.

Fast forward 22 years after the game's release and after seeing my [RTX Remix mod](https://www.moddb.com/mods/duoprincess-remixed), RadicalR reached out to me with all the files and notes he could dig up from old hard drives that he still had. With a little research of _those_ tools and other EasyGameStation [projects](https://github.com/search?q=Recettear&type=repositories), the game's translation could finally be seen through to the end!

## Installation:
Download the latest patcher from the [Releases](https://github.com/Etokapa/Duo-Princess-English-Translation-Patch/releases/latest) page. 

Extract and run `DPEP.bat` from the DuoPrincess folder.
It will automatically patch the game to v1.04 and apply the English patch.

## Credits:
Translation, Textures, Playtesting, File Research, Scripts, Patcher: Etokapa

Original File Research, Extraction/Repacking Scripts: RadicalR

Special Thanks: Aaron Tang, fredrik, My Dilsnufus, Aerlinah, Z-Virus, ViruX, Arkaether Avalon, Daichi

## Notes:
Please support the developers by purchasing a copy of the game.

* Tools and other scripts I used are in the [Tools](https://github.com/Etokapa/Duo-Princess-English-Translation-Patch/tree/main/Tools) folder.
* Editing dialogue works as follows: EXE -> JP BIN -> JP TXT -> EN TXT -> EN BIN -> EXE
* Dialogue is packed inside the game's EXE in the `MSG` folder and can be extracted/repacked with Resource Hacker as a `bin` file.
* Use `duomsg.exe file.bin` to convert the extracted JP `bin` into a readable JP `txt` format.
* Translate the Japanese text into English. `/` are used instead of ` `(spaces) because they use less room. 
* Use the `duomsgEN.pyw` GUI to convert English `txt` to `bin`.
* Finally, use Resource Hacker to replace the Japanese MSG `bin` files with the English ones.
* The dialogue font is [DFPMaruMoji](https://www.dynacw.com/en/product/product_download_detail.aspx?sid=222).
* Use `duopack.exe -u BMPDATA.BIN bmp_extracted` or `py DP_extract_bmpdata.py bmpdata.bin extracted` to extract textures. Textures are tga and bmp files.
* `DP_repack_bmpdata.py` can be used to repack textures back into the bin, but the game will read files from the `DATA` folder if present.
* 3D models are in [X](https://learn.microsoft.com/en-us/windows/win32/direct3d9/x-files--legacy-) format.
* The game runs at 640x480 and while it's possible to get 3D rendering to run at higher resolutions, the implemention is incomplete and would need someone more skilled than me to get 2D scenes to scale properly.
* Music are `WAV` files in the `BGM` folder.
* Sound Effects are WAV files packed inside the game's EXE under `WAVE`.
* This patch is not compatible with the RTX Remix mod (yet).
* Improvements welcome.
