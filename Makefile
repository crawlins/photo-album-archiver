# Android capture app. Needs a JDK (17 or later) and the Android SDK; see README.md.
ANDROID_APP_ID := org.bit63.albumarchiver
APK := android/app/build/outputs/apk/debug/app-debug.apk

.PHONY: apk install-apk serve

# Build the debug APK.
apk:
	cd android && ./gradlew assembleDebug
	@echo "APK: $(APK)"

# Build, install over USB on the connected phone (keeping its data), and start the app.
install-apk: apk
	@command -v adb >/dev/null || { echo "adb not found: add the Android SDK's platform-tools to PATH"; exit 1; }
	@test "$$(adb devices | grep -c 'device$$')" -ge 1 || { echo "No phone found: connect it by USB with USB debugging on, and accept the prompt on the phone"; exit 1; }
	adb install -r $(APK)
	adb shell monkey -p $(ANDROID_APP_ID) -c android.intent.category.LAUNCHER 1 >/dev/null

# Run the upload server on every network interface, so a phone on the same
# Wi-Fi can reach it at http://<this machine's address>:$(PORT). Needs the
# backend installed (pip install -e backend) and a token for the phone
# (albumserver token create <name>).
PORT ?= 8080
serve:
	@command -v albumserver >/dev/null || { echo "albumserver not found: run pip install -e backend"; exit 1; }
	@echo "Phones can reach this server at:"; for ip in $$(hostname -I 2>/dev/null); do echo "  http://$$ip:$(PORT)"; done
	ALBUMSERVER_HOST=0.0.0.0 ALBUMSERVER_PORT=$(PORT) albumserver serve
