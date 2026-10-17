package org.bit63.albumarchiver.ui.albums

import com.google.common.truth.Truth.assertThat
import org.bit63.albumarchiver.data.Page
import org.bit63.albumarchiver.data.PageFeedback
import org.junit.Test
import java.time.Instant

class LeaveAlbumGuardTest {
    private fun page(n: Int, shots: Int = 2) = Page("p$n", "a", n, shotCount = shots)
    private fun fb(n: Int, state: String = "done") = PageFeedback("p$n", "a", state, null, "[]", Instant.EPOCH)

    @Test fun `work left counts warned pages and pages still to be checked`() {
        val pages = listOf(page(1), page(2), page(3), page(4), page(5), page(6, shots = 0))
        val feedback = listOf(fb(1), fb(2), fb(3, "processing"), fb(4, "ready"))
        // p1 done and warned; p2 done but a shot is queued; p3, p4 not finished; p5 never processed; p6 empty.
        val w = albumWorkLeft(pages, feedback, queued = listOf("p2"), warned = setOf("p1", "p3"), serverConfigured = true)
        assertThat(w).isEqualTo(AlbumWorkLeft(warned = 1, pending = 4, serverConfigured = true))
        assertThat(w.ask).isTrue()
        assertThat(w.text).isEqualTo("1 page needs another shot and 4 pages are still being checked.")
        assertThat(AlbumWorkLeft(2, 0, true).text).isEqualTo("2 pages need another shot.")
        assertThat(AlbumWorkLeft(0, 1, true).text).isEqualTo("1 page is still being checked.")
    }

    @Test fun `nothing is asked without a server or when every page has clean feedback`() {
        val pages = listOf(page(1), page(2))
        assertThat(albumWorkLeft(pages, listOf(fb(1), fb(2)), emptyList(), emptySet(), true).ask).isFalse()
        assertThat(albumWorkLeft(pages, listOf(fb(1)), emptyList(), setOf("p1"), serverConfigured = false).ask).isFalse()
        assertThat(albumWorkLeft(emptyList(), emptyList(), emptyList(), emptySet(), true).ask).isFalse()
    }

    @Test fun `the guard leaves at once, or asks once and then stays or leaves`() {
        val g = LeaveAlbumGuard()
        var left = 0
        var overview = 0
        g.request(AlbumWorkLeft(0, 0, true)) { left++ }
        assertThat(left).isEqualTo(1)
        assertThat(g.leaving).isNull()

        g.request(AlbumWorkLeft(1, 0, true)) { left++ }
        assertThat(left).isEqualTo(1)
        assertThat(g.leaving).isNotNull()
        g.stay { overview++ }
        assertThat(overview).isEqualTo(1)
        assertThat(left).isEqualTo(1)
        assertThat(g.leaving).isNull()

        g.request(AlbumWorkLeft(0, 2, true)) { left++ }
        g.leaveAnyway()
        assertThat(left).isEqualTo(2)
        assertThat(g.leaving).isNull()
        g.leaveAnyway()  // nothing open: nothing happens
        assertThat(left).isEqualTo(2)

        g.request(AlbumWorkLeft(1, 1, true)) { left++ }
        g.dismiss()
        assertThat(left).isEqualTo(2)
        assertThat(g.leaving).isNull()
    }
}
