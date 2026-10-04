package org.bit63.albumarchiver

import android.app.Application

/** Robolectric's application: skips [AlbumArchiverApp]'s keystore-backed production wiring. */
class TestApplication : Application()
