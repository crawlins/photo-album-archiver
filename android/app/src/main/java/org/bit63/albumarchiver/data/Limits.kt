package org.bit63.albumarchiver.data

/** The album limits the server enforces too (Requirements 4.9 and 5.5). */
object Limits {
    const val MAX_PAGES_PER_ALBUM = 500
    const val MAX_SHOTS_PER_PAGE = 25

    /** Below this much free storage the capture screen warns (Requirement 7.4). */
    const val LOW_STORAGE_BYTES = 500L * 1024 * 1024
}
