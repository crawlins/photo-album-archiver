package org.bit63.albumarchiver.ui.capture

import org.bit63.albumarchiver.data.Page
import org.bit63.albumarchiver.data.PageFeedback
import org.bit63.albumarchiver.data.PageWarning

/**
 * The notice on the capture screen for pages the user has left (single-shot
 * glare spec, Requirement 3.1): the lowest-numbered such page and how many
 * more there are.
 */
data class OtherWarned(val pageId: String, val number: Int, val more: Int) {
    val text: String get() = if (more == 0) "Page $number needs another shot" else "Page $number needs another shot, and $more more"
}

/** The codes that ask for another shot; uncovered page alone does not, the page overview marks it (Requirement 3.6). */
private val ANOTHER_SHOT_CODES = setOf(PageWarning.CODE_SINGLE, PageWarning.CODE_GLARE)

/**
 * The other warned pages, lowest number first: pages with a red dot
 * ([warned], which already leaves out pages with shots still queued) whose
 * feedback asks for another shot, other than the [targetPageId] the user is
 * shooting.
 */
fun otherWarnedPages(pages: List<Page>, feedback: List<PageFeedback>, warned: Set<String>, targetPageId: String?): List<Page> {
    val asking = feedback.filter { fb -> fb.warnings.any { it.code in ANOTHER_SHOT_CODES } }.map { it.pageId }.toSet()
    return pages.filter { it.id != targetPageId && it.id in warned && it.id in asking }.sortedBy { it.position }
}

fun List<Page>.toNotice(): OtherWarned? = firstOrNull()?.let { OtherWarned(it.id, it.position, size - 1) }

/**
 * Decides when to vibrate (Requirement 3.3): once for each page that becomes
 * an other warned page while the screen is open, and not again for it until
 * its warnings have cleared and returned. Pages already warned when the
 * screen opens did not become so while it was open.
 */
class NewlyWarned {
    private var seen: Set<String>? = null

    /** The pages in [current] that were not other warned pages at the last update. */
    fun update(current: Set<String>): Set<String> {
        val before = seen
        seen = current
        return if (before == null) emptySet() else current - before
    }
}
