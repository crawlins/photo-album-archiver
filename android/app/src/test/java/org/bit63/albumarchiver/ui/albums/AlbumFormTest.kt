package org.bit63.albumarchiver.ui.albums

import com.google.common.truth.Truth.assertThat
import org.bit63.albumarchiver.data.Album
import org.bit63.albumarchiver.data.PageSize
import org.junit.Test
import java.time.Instant
import java.time.LocalDate

class AlbumFormTest {
    @Test fun `a new album defaults to Album plus today's date and US letter`() {
        val f = AlbumForm.forNew(LocalDate.of(2026, 10, 4))
        assertThat(f.name).isEqualTo("Album 2026-10-04")
        assertThat(f.choice).isEqualTo(SizeChoice.LETTER)
        assertThat(f.isValid).isTrue()
        assertThat(f.pageSize).isEqualTo(PageSize.Preset.LETTER)
    }

    @Test fun `a blank name is rejected`() {
        for (name in listOf("", "   ", "\t")) {
            val f = AlbumForm(name = name)
            assertThat(f.nameError).isNotNull()
            assertThat(f.isValid).isFalse()
        }
    }

    @Test fun `presets need no dimensions`() {
        assertThat(AlbumForm("x", SizeChoice.A4).pageSize).isEqualTo(PageSize.Preset.A4)
        assertThat(AlbumForm("x", SizeChoice.A3, width = "junk").pageSize).isEqualTo(PageSize.Preset.A3)
        assertThat(AlbumForm("x", SizeChoice.A3, width = "junk").widthError).isNull()
    }

    @Test fun `custom sizes need two positive numbers`() {
        val missing = AlbumForm("x", SizeChoice.CUSTOM)
        assertThat(missing.widthError).isEqualTo("Required")
        assertThat(missing.heightError).isEqualTo("Required")
        assertThat(missing.isValid).isFalse()
        assertThat(missing.pageSize).isNull()

        val bad = AlbumForm("x", SizeChoice.CUSTOM, width = "ten", height = "0")
        assertThat(bad.widthError).isEqualTo("Not a number")
        assertThat(bad.heightError).isEqualTo("Must be more than 0")
        assertThat(bad.isValid).isFalse()

        val ok = AlbumForm("x", SizeChoice.CUSTOM, width = "254", height = "305", unit = PageSize.Unit.MM)
        assertThat(ok.isValid).isTrue()
        assertThat(ok.pageSize!!.text).isEqualTo("254x305mm")
        assertThat(AlbumForm("x", SizeChoice.CUSTOM, width = "8,5", height = "11").pageSize!!.text).isEqualTo("8.5x11in")
    }

    @Test fun `editing prefills the album's values`() {
        fun album(size: String) = Album("id", "Family", size, Instant.EPOCH)
        assertThat(AlbumForm.forEdit(album("letter")).choice).isEqualTo(SizeChoice.LETTER)
        assertThat(AlbumForm.forEdit(album("a4")).choice).isEqualTo(SizeChoice.A4)
        assertThat(AlbumForm.forEdit(album("a3")).choice).isEqualTo(SizeChoice.A3)
        val custom = AlbumForm.forEdit(album("254x305mm"))
        assertThat(custom).isEqualTo(AlbumForm("Family", SizeChoice.CUSTOM, "254", "305", PageSize.Unit.MM))
    }

    @Test fun `a custom size equal to a preset stays custom when edited`() {
        val f = AlbumForm.forEdit(Album("id", "Family", "8.5x11in", Instant.EPOCH))
        assertThat(f.choice).isEqualTo(SizeChoice.CUSTOM)
        assertThat(f.pageSize!!.text).isEqualTo("8.5x11in")
    }

    @Test fun `an unreadable stored size falls back to the default`() {
        assertThat(AlbumForm.forEdit(Album("id", "F", "garbage", Instant.EPOCH)).choice).isEqualTo(SizeChoice.LETTER)
    }

    @Test fun `upload state per album`() {
        assertThat(uploadState("a", pendingOps = 0, running = true, headAlbumId = "a")).isEqualTo(UploadState.UPLOADED)
        assertThat(uploadState("a", pendingOps = 3, running = true, headAlbumId = "a")).isEqualTo(UploadState.UPLOADING)
        assertThat(uploadState("a", pendingOps = 3, running = true, headAlbumId = "b")).isEqualTo(UploadState.WAITING)
        assertThat(uploadState("a", pendingOps = 3, running = false, headAlbumId = "a")).isEqualTo(UploadState.WAITING)
    }
}
