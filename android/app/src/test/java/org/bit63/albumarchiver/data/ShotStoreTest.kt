package org.bit63.albumarchiver.data

import com.google.common.truth.Truth.assertThat
import org.junit.Assert.assertThrows
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.io.IOException

class ShotStoreTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun store(free: Long = Long.MAX_VALUE) = ShotStore(File(tmp.root, "shots")) { free }

    @Test fun `shot paths follow album, page, shot`() {
        val s = store()
        assertThat(s.shotFile("a", "p", "s").relativeTo(s.root).path).isEqualTo("a/p/s.jpg")
    }

    @Test fun `temp files are outside page folders and end in tmp`() {
        val t = store().newTempFile("s1")
        assertThat(t.name).isEqualTo("s1.jpg.tmp")
        assertThat(t.parentFile!!.isDirectory).isTrue()
    }

    @Test fun `finish hashes the file and commit renames it into place`() {
        val s = store()
        val temp = s.newTempFile("s1").apply { writeText("abc") }
        val w = s.finish(temp)
        assertThat(w.sha256).isEqualTo("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        assertThat(w.bytes).isEqualTo(3)
        val dest = s.shotFile("a", "p", "s1")
        s.commit(temp, dest)
        assertThat(dest.readText()).isEqualTo("abc")
        assertThat(temp.exists()).isFalse()
    }

    @Test fun `finish refuses a missing or empty file`() {
        val s = store()
        assertThrows(IOException::class.java) { s.finish(s.newTempFile("none")) }
        val empty = s.newTempFile("empty").apply { writeBytes(ByteArray(0)) }
        assertThrows(IOException::class.java) { s.finish(empty) }
    }

    @Test fun `writeVerified keeps a matching download and discards a corrupt one`() {
        val s = store()
        val dest = s.shotFile("a", "p", "s1")
        val good = ShotStore.sha256("abc".byteInputStream())
        assertThat(s.writeVerified("abX".byteInputStream(), dest, good)).isFalse()
        assertThat(dest.exists()).isFalse()
        assertThat(dest.parentFile!!.listFiles()!!.toList()).isEmpty()
        assertThat(s.writeVerified("abc".byteInputStream(), dest, good.uppercase())).isTrue()
        assertThat(dest.readText()).isEqualTo("abc")
    }

    @Test fun `cleanUp removes temp files and unknown shots but keeps known ones`() {
        val s = store()
        val known = s.shotFile("a", "p", "known").apply { parentFile!!.mkdirs(); writeText("k") }
        val orphan = s.shotFile("a", "p", "orphan").apply { writeText("o") }
        val partial = File(known.parentFile, "x.jpg.tmp").apply { writeText("t") }
        val capturing = s.newTempFile("c").apply { writeText("c") }
        val emptyDir = s.pageDir("a", "empty").apply { mkdirs() }

        val removed = s.cleanUp(setOf(known.absolutePath))

        assertThat(removed).isEqualTo(3)
        assertThat(known.exists()).isTrue()
        assertThat(orphan.exists()).isFalse()
        assertThat(partial.exists()).isFalse()
        assertThat(capturing.exists()).isFalse()
        assertThat(emptyDir.exists()).isFalse()
    }

    @Test fun `cleanUp on a fresh install does nothing`() {
        assertThat(store().cleanUp(emptySet())).isEqualTo(0)
    }

    @Test fun `low storage below 500 MB`() {
        assertThat(store(free = 499L * 1024 * 1024).isLowOnSpace()).isTrue()
        assertThat(store(free = 500L * 1024 * 1024).isLowOnSpace()).isFalse()
    }

    @Test fun `deleting a page or album removes its folder`() {
        val s = store()
        s.shotFile("a", "p1", "s").apply { parentFile!!.mkdirs(); writeText("1") }
        s.shotFile("a", "p2", "s").apply { parentFile!!.mkdirs(); writeText("2") }
        s.deletePage("a", "p1")
        assertThat(s.pageDir("a", "p1").exists()).isFalse()
        assertThat(s.pageDir("a", "p2").exists()).isTrue()
        s.deleteAlbum("a")
        assertThat(s.albumDir("a").exists()).isFalse()
    }
}
