package org.bit63.albumarchiver.ui

import com.google.common.truth.Truth.assertThat
import org.bit63.albumarchiver.data.Page
import org.bit63.albumarchiver.data.Shot
import org.junit.Test
import java.time.Instant

/** Labels and availability of the shot actions (shot-actions Requirements 2, 5.5 to 5.7, 6.3, 6.4). */
class ShotActionsMenuTest {
    private fun shots(n: Int, page: String = "p") =
        (1..n).map { Shot("s$it", "a", page, "/x/s$it.jpg", "h", 1, Instant.EPOCH.plusSeconds(it.toLong())) }

    private fun pages(vararg counts: Int) = counts.mapIndexed { i, n -> Page("p${i + 1}", "a", i + 1, n) }

    /** The menu for the shot at 1-based [at] on page [page] (1-based). */
    private fun menu(pages: List<Page>, page: Int, at: Int): ShotActionsMenu {
        val p = pages[page - 1]
        return ShotActionsMenu.of(shots(p.shotCount, p.id).drop(at - 1), p, pages)
    }

    @Test fun `labels name the run and the target page`() {
        val m = menu(pages(5, 2), 1, 3)
        assertThat(m.title).isEqualTo("Page 1, photo 3 of 5")
        assertThat(m.deleteRunLabel).isEqualTo("Delete this photo and the 2 after it")
        assertThat(m.moveLabel).isEqualTo("Move this photo and the 2 after it to page 2")
        assertThat(m.newPageLabel).isEqualTo("Create a new page with this photo and the 2 after it")
        assertThat(listOf(m.deleteRunReason, m.moveReason, m.newPageReason)).containsExactly(null, null, null)
    }

    @Test fun `on the last page the move goes to a new page`() {
        val m = menu(pages(1, 4), 2, 2)
        assertThat(m.toNewPage).isTrue()
        assertThat(m.moveLabel).isEqualTo("Move this photo and the 2 after it to a new page 3")
    }

    @Test fun `for the last photo of a page the run actions are worded for one photo`() {
        val m = menu(pages(3, 1), 1, 3)
        assertThat(m.deleteRunReason).isEqualTo(ShotActionsMenu.NO_PHOTOS_AFTER)
        assertThat(m.moveLabel).isEqualTo("Move this photo to page 2")
        assertThat(m.newPageLabel).isEqualTo("Create a new page with this photo")
        assertThat(m.moveReason).isNull()
        assertThat(m.newPageReason).isNull()
    }

    @Test fun `the whole last page cannot move or become a new page`() {
        val m = menu(pages(1, 3), 2, 1)
        assertThat(m.moveReason).isEqualTo(ShotActionsMenu.WHOLE_LAST_PAGE)
        assertThat(m.newPageReason).isEqualTo(ShotActionsMenu.WHOLE_PAGE)
        assertThat(m.deleteRunRemovesPage).isFalse()
    }

    @Test fun `a whole inner page can move into the next one, and deleting it removes the page`() {
        val m = menu(pages(1, 3, 1), 2, 1)
        assertThat(m.moveReason).isNull()
        assertThat(m.newPageReason).isEqualTo(ShotActionsMenu.WHOLE_PAGE)
        assertThat(m.deleteRunRemovesPage).isTrue()
        assertThat(m.deleteShotRemovesPage).isFalse()
        assertThat(menu(pages(1, 1, 1), 2, 1).deleteShotRemovesPage).isTrue()
        assertThat(menu(pages(1, 1), 2, 1).deleteShotRemovesPage).isFalse()
    }

    @Test fun `a move that would put more than 25 photos on the next page is disabled`() {
        assertThat(menu(pages(3, 23), 1, 2).moveReason).isNull()
        assertThat(menu(pages(3, 23), 1, 1).moveReason).isEqualTo("Page 2 would have more than 25 photos")
    }

    @Test fun `new pages are disabled when the album has 500 pages`() {
        val full = (1..500).map { Page("p$it", "a", it, 2) }
        val last = ShotActionsMenu.of(shots(2, "p500").drop(1), full.last(), full)
        assertThat(last.moveReason).isEqualTo(ShotActionsMenu.ALBUM_FULL)
        assertThat(last.newPageReason).isEqualTo(ShotActionsMenu.ALBUM_FULL)
        // Moving to an existing next page needs no new page.
        val inner = ShotActionsMenu.of(shots(2, "p1").drop(1), full.first(), full)
        assertThat(inner.moveReason).isNull()
        assertThat(inner.newPageReason).isEqualTo(ShotActionsMenu.ALBUM_FULL)
    }
}
