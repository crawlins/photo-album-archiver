package org.bit63.albumarchiver.ui.capture

import com.google.common.truth.Truth.assertThat
import org.bit63.albumarchiver.data.Page
import org.bit63.albumarchiver.data.PageFeedback
import org.junit.Test
import java.time.Instant

class OtherWarnedTest {
    private val pages = (1..5).map { Page("p$it", "a", it, shotCount = 2) }
    private fun fb(page: String, vararg codes: String) =
        PageFeedback(page, "a", "done", null, codes.joinToString(",", "[", "]") { """{"code":"$it","message":"m","regions":[]}""" }, Instant.EPOCH)

    private val feedback = listOf(
        fb("p1", "incomplete_coverage"),
        fb("p2", "single_coverage"),
        fb("p3", "glare", "photos_dropped"),
        fb("p4", "single_coverage", "incomplete_coverage"),
        fb("p5", "single_coverage"),
    )

    @Test fun `other warned pages leave out the target page and uncovered-only pages, lowest number first`() {
        val warned = setOf("p1", "p2", "p3", "p4", "p5")
        val others = otherWarnedPages(pages.reversed(), feedback, warned, targetPageId = "p5")
        assertThat(others.map { it.id }).containsExactly("p2", "p3", "p4").inOrder()
        assertThat(others.toNotice()).isEqualTo(OtherWarned("p2", 2, 2))
        assertThat(others.toNotice()!!.text).isEqualTo("Page 2 needs another shot, and 2 more")
    }

    @Test fun `only pages with a red dot count, and none means no notice`() {
        // p2's shots are still queued, so it has no red dot yet.
        val others = otherWarnedPages(pages, feedback, setOf("p1", "p3"), targetPageId = "p5")
        assertThat(others.toNotice()).isEqualTo(OtherWarned("p3", 3, 0))
        assertThat(others.toNotice()!!.text).isEqualTo("Page 3 needs another shot")
        assertThat(otherWarnedPages(pages, feedback, setOf("p1"), "p5").toNotice()).isNull()
        assertThat(otherWarnedPages(pages, feedback, setOf("p5"), "p5").toNotice()).isNull()
    }

    @Test fun `a page vibrates once when it becomes warned, and again only after clearing`() {
        val t = NewlyWarned()
        assertThat(t.update(setOf("p2"))).isEmpty()  // already warned when the screen opened
        assertThat(t.update(setOf("p2", "p3"))).containsExactly("p3")
        assertThat(t.update(setOf("p2", "p3"))).isEmpty()
        assertThat(t.update(setOf("p2"))).isEmpty()  // p3 cleared
        assertThat(t.update(setOf("p2", "p3"))).containsExactly("p3")  // and came back
    }
}
