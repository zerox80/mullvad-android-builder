# Mullvad Android Builder

Ein eigener Docker-Builder für die Mullvad-Android-App und kompatible Forks mit
Kotlin/Gradle und Rust. Standardmäßig baut er den aktuellen Stand von
`zerox80/mullvadvpn-app`, Branch `android-pause-vpn`, als `ossProdRelease` für ARM64
(z. B. Google Pixel 9a). Ein exakter Commit lässt sich ebenfalls angeben.

## Enthalten

- Zum Quellcode passende Mullvad-Toolchain, per Image-Digest festgelegt.
- Java 21, Rust 1.97.1, Android SDK 37, Build Tools 37.0.0, NDK 27.3.13750724.
- Dauerhafte Docker-Volumes für Quellcode, Gradle, Cargo Registry, Cargo Git und
  Rust-Kompilate. ARM64 und Builds für alle Architekturen verwenden getrennte Standard-Caches.
- Read-only-Zugriff auf lokale Quellen; gebaut werden ausschließlich Git-Commits.
- Prüfung von Paketname, Release-Modus, tatsächlichen ELF-Architekturen und
  APK-Ausrichtung für 16-KiB-Seiten.
- APK, SHA-256-Prüfsumme, Build-Log und `build-info.json` mit Commit und Toolchain.
- Windows-Skripte zum Bauen über WSL, Signieren und optionalen Installieren per ADB.
- Offline-Prüfung und Tests für Quellenwechsel und APK-Validierung.

Die Toolchain stammt aus dem offiziellen Mullvad-Build-Image. Dieses Repository
ergänzt den Build-Ablauf und baut daraus ein eigenes lokales Image. Das ist auf
Mullvad und kompatible Forks ausgelegt; beliebige Android-Projekte können andere
SDKs, JDKs oder Build-Schritte benötigen. Das Image ist `linux/amd64`.

## Voraussetzungen

Linux mit Bash und Docker Engine, oder Windows mit WSL2/Ubuntu und einer in WSL
erreichbaren Linux-Docker-Engine. Auf dem eingerichteten Rechner funktioniert
bereits die Docker Engine in Ubuntu; Docker Desktop muss dafür nicht laufen.
Für den ersten Download und ungefüllte Dependency-Caches wird Internet benötigt.
Die enthaltene Toolchain ist mehrere GB groß. Standard: vier Worker, 4 GiB
Gradle-Heap; ausreichend zusätzlichen RAM für Rust und das Betriebssystem vorsehen.

## Windows: ein Befehl zum Bauen

In PowerShell im Repository:

```powershell
.\scripts\Build.ps1
```

Das lädt bei Bedarf die festgelegte Basis, baut das eigene Builder-Image und
erzeugt die unsignierte Release-APK unter `artifacts/`.

Lokalen letzten Commit bauen:

```powershell
.\scripts\Build.ps1 -Source C:\Pfad\zu\mullvadvpn-app
```

Exakten Commit mit den bereits vorhandenen Caches dieses Rechners bauen:

```powershell
.\scripts\Build.ps1 -Source ..\mullvadvpn-app `
  -Ref 9a848cd421241d4826ad6183f7a37ab592ba2c85 -ReuseWorkspaceCaches
```

`-ReuseWorkspaceCaches` ist nur für den bereits eingerichteten ARM64-Arbeitsplatz
gedacht. Auf anderen Rechnern einfach weglassen. Es kopiert keine Schlüssel.
Nicht committete Änderungen im Quell-Checkout fließen nicht in die APK ein.

Nur prüfen, einschließlich Gradle-Taskgraph, ohne APK zu assemblieren:

```powershell
.\scripts\Build.ps1 -Source ..\mullvadvpn-app -ReuseWorkspaceCaches -Check -Offline
```

`-Doctor` zeigt die installierten Toolchains ohne Quell-Checkout.
Mit `-Architecture all` werden ARM64, ARMv7, x86 und x86_64 gebaut.
Mit `-OutputDirectory`, `-Workers` und `-Distribution` lassen sich Ausgabe,
Parallelität und WSL-Distribution wählen.

## Linux / direkt in WSL

```bash
bash build.sh
bash build.sh --source /path/to/mullvadvpn-app --ref HEAD
bash build.sh --ref 9a848cd421241d4826ad6183f7a37ab592ba2c85
bash build.sh --arch all --out ./artifacts-all
bash build.sh --source /path/to/mullvadvpn-app --check --offline
bash build.sh --doctor
```

Remote-Quellen müssen über eine öffentliche HTTPS-URL erreichbar sein. Für private
Quellen einen bereits authentifizierten lokalen Git-Checkout mit `--source`
verwenden. Keine Tokens in Repository-URLs übergeben.

`--offline` deaktiviert das Netzwerk des Build-Containers und setzt zusätzlich
Gradle und Cargo auf Offline-Betrieb. Das Basis-Image und alle benötigten Quellen
und Abhängigkeiten müssen bereits lokal vorhanden sein. Bei Offline-Builds aus
einem Remote-Cache einen exakten Commit angeben; ein Branch im Cache kann älter sein.
Ein erfolgreicher `--check` bestätigt Konfiguration und Taskgraph, ersetzt aber
keinen vollständigen APK-Build.

## Ausgabe

```text
artifacts/<commit12>/<arch>/<UTC-Zeit>/
  MullvadVPN-<version>-<arch>-release-unsigned.apk
  SHA256SUMS
  build-info.json
  apk-badging.txt
  build.log
```

Jeder Lauf erhält ein eigenes Verzeichnis. Fehlgeschlagene Gradle-Aufrufe werden
als Fehler zurückgegeben; es wird keine APK aus einem vorherigen Lauf exportiert.
Die Prüfungen lesen den ELF-Header von `libmullvad_jni.so`, da weitere gebündelte
Libraries zusätzliche ABIs in der allgemeinen APK-Metadatenliste anzeigen können.
Der Build verwendet den Commit samt Lockfiles; Abhängigkeiten werden nicht
automatisch auf andere Versionen aktualisiert.

## Signieren unter Windows

Der Build-Container erhält keinen Signierschlüssel. Vorhandenen Schlüssel nutzen,
damit spätere Updates zur installierten App passen:

```powershell
.\scripts\Sign-Apk.ps1 -Apk C:\Pfad\zur\release-unsigned.apk `
  -Keystore C:\Privat\mullvad-release.p12 -Alias release
```

Das Passwort wird verdeckt abgefragt. Alternativ kann eine bereits mit
`ConvertFrom-SecureString` unter demselben Windows-Benutzer erzeugte DPAPI-Datei
über `-ProtectedPasswordFile` angegeben werden. Das Skript prüft vorhandene
Build-Metadaten, richtet die APK aus, signiert sie und überprüft Signatur und
Ausrichtung. Es erzeugt eine `.signing-info.json` und `.sha256` neben der signierten
APK. Schlüssel und Passwortdateien außerhalb dieses Repositories aufbewahren.
Ein anderer Windows-Benutzer oder Rechner kann die DPAPI-Datei gewöhnlich nicht
entschlüsseln; Schlüssel und ein unabhängig nutzbares Passwort sicher behalten.

Optional auf einem verbundenen Android-Gerät installieren:

```powershell
.\scripts\Install-Apk.ps1 -Apk C:\Pfad\zur\release.apk -Serial DEINE_ADB_SERIENNUMMER -Launch
```

Ohne `-Serial` muss genau ein autorisiertes Gerät verbunden sein. Das Skript nutzt
`adb install -r`. Ein Signaturkonflikt wird als Fehler ausgegeben; es deinstalliert
die vorhandene App nicht automatisch. Bei APKs aus GitHub Actions die Datei
`release-info.json` neben der APK aufbewahren; das Installationsskript prüft damit
die APK-Prüfsumme.

## Cache-Verhalten

Standard-Volumes heißen `mullvad-builder-arm64-*` bzw. `mullvad-builder-all-*`.
Für unabhängige Projekte/Builds lässt sich `CACHE_PREFIX` setzen. Die Variablen
`SOURCE_VOLUME`, `GRADLE_VOLUME`, `CARGO_REGISTRY_VOLUME`, `CARGO_GIT_VOLUME` und
`CARGO_TARGET_VOLUME` überschreiben einzelne Namen. Das Skript sperrt den
gemeinsam verwendeten Cargo-Target-Cache gegen parallele Builder-Läufe. Andere
Tools, die diese Sperre nicht beachten, dürfen dieselben Caches nicht gleichzeitig
verändern. Für komplett unabhängige parallele Builds einen anderen Präfix nutzen.

Quellcode und native Android-Ausgaben liegen im Source-Volume unter `/build/repo`.
`CARGO_TARGET_DIR=/cargo-target/target` hält unter anderem die Cargo-Versionshelfer
im Target-Volume. Auch `/root/.cargo/git` ist persistent; dadurch verschwindet der
Git-Dependency-Cache nicht beim Löschen eines Containers.

## Toolchain aktualisieren

Wenn ein neuer App-Commit eine andere Toolchain verlangt, verweigert der Builder
den Build mit der benötigten Image-Referenz. Den Inhalt von
`building/android-container-image.txt` aus diesem App-Commit nach `base-image.txt`
übernehmen und erneut bauen. Den Wert an einer vertrauenswürdigen Quelle prüfen.
Das Builder-Image wird automatisch neu gebaut. Bestehende Caches bleiben erhalten.
Rust-/Gradle-Lockfiles aus dem gewählten App-Commit bleiben maßgeblich.

## Automatische APKs mit GitHub Actions

Der Workflow `examples/android-pause-apk.yml` gehört in das App-Repository unter
`.github/workflows/`. Bei jedem Push auf `android-pause-vpn` baut er genau den
auslösenden Commit auf einem GitHub-Runner. Der private Builder wird über einen
eigenen read-only SSH-Deploy-Key ausgecheckt. Gradle-/Rust-Caches werden zwischen
Läufen wiederhergestellt. Nach erfolgreichem Build wird die APK getrennt signiert
und für 14 Tage als Workflow-Artefakt bereitgestellt, zusammen mit Prüfsummen,
Build-Log, Commit und Zertifikat-Fingerprint. Der Rechner und das Handy müssen
dafür nicht eingeschaltet sein.

Erforderliche Actions Secrets im **App-Repository**:

| Secret | Inhalt |
| --- | --- |
| `MULLVAD_BUILDER_SSH_KEY` | Privater SSH-Schlüssel; zugehöriger öffentlicher Deploy-Key im Builder-Repo, read-only |
| `ANDROID_RELEASE_KEYSTORE_BASE64` | Base64-Inhalt des bestehenden Release-Keystores |
| `ANDROID_RELEASE_KEYSTORE_PASSWORD` | Passwort dieses Keystores |

Das App-Repository erhält keinen allgemeinen GitHub-PAT. Der Signierschlüssel
wird erst im Signierschritt als temporäre Datei bereitgestellt, nicht beim
Kompilieren und nicht im Docker-Image oder Actions-Cache. `container/builder.py sign`
prüft die APK gegen die Build-Metadaten und signiert mit `apksigner`; die signierte
Ausgabe liegt in einem eigenen `release/`-Verzeichnis.

Der Workflow verwendet den aktuellen `main`-Stand des privaten Builder-Repositories.
Externe Actions sind auf Commit-SHAs festgelegt. Die Build-Metadaten erfassen
zusätzlich den SHA-256 des tatsächlich ausgeführten Builder-Skripts.
Bei leeren Caches dauert der erste vollständige Build deutlich länger.

## Tests

Unter Linux/WSL:

```bash
bash -n build.sh
python3 -m unittest discover -s tests -v
```

Im Builder-Repository führt GitHub Actions bei jedem Push diese Tests und eine
Syntaxprüfung der PowerShell-Skripte aus. Der vollständige automatische APK-Build
wird vom separaten Workflow im App-Repository ausgelöst.

Grundlagen: [Docker-Volumes](https://docs.docker.com/engine/storage/volumes/),
[festgelegte Basis-Images](https://docs.docker.com/build/building/best-practices/#pin-base-image-versions).
