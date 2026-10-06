package org.bit63.albumarchiver.ui.review

import com.google.common.truth.Truth.assertThat
import org.bit63.albumarchiver.data.Page
import org.junit.Test

class LandingTest {
    private fun page(id: String, position: Int, shots: Int) = Page(id, "a", position, shots)

    @Test fun `slots list every shot of every page in order, unloaded pages by their count`() {
        val s = slots(listOf(page("p2", 2, 1), page("p1", 1, 2), page("p3", 3, 0)))
        assertThat(s).containsExactly(Slot("p1", 0), Slot("p1", 1), Slot("p2", 0)).inOrder()
    }

    @Test fun `after deleting a shot the next one is shown`() {
        // p1 had shots 0,1,2; shot 1 deleted.
        val after = slots(listOf(page("p1", 1, 2), page("p2", 2, 1)))
        assertThat(landingAfterDeletion(after, 1)).isEqualTo(Slot("p1", 1))
    }

    @Test fun `deleting a page's last shot moves to the next page's first`() {
        val after = slots(listOf(page("p1", 1, 1), page("p2", 2, 2)))
        assertThat(landingAfterDeletion(after, 1)).isEqualTo(Slot("p2", 0))
    }

    @Test fun `with no next shot the previous one is shown`() {
        val after = slots(listOf(page("p1", 1, 2)))
        assertThat(landingAfterDeletion(after, 2)).isEqualTo(Slot("p1", 1))
    }

    @Test fun `deleting a whole page lands on the following page`() {
        // p2 (2 shots) deleted from p1(1), p2(2), p3(1): p2 started at slot 1.
        val after = slots(listOf(page("p1", 1, 1), page("p3", 2, 1)))
        assertThat(landingAfterDeletion(after, 1)).isEqualTo(Slot("p3", 0))
    }

    @Test fun `an empty album goes back to the overview`() {
        assertThat(landingAfterDeletion(emptyList(), 0)).isNull()
    }
}
