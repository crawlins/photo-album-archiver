package org.bit63.albumarchiver.ui.albums

import org.bit63.albumarchiver.data.Album
import org.bit63.albumarchiver.data.PageSize
import org.bit63.albumarchiver.data.PageSizeValidation
import java.time.LocalDate
import java.time.format.DateTimeFormatter

/** Which page size the form has selected. */
enum class SizeChoice { LETTER, A4, A3, CUSTOM }

/**
 * The create / edit album form (Requirements 2.3 to 2.5, 2.8), kept free of
 * Compose so its validation can be unit tested.
 */
data class AlbumForm(
    val name: String,
    val choice: SizeChoice = SizeChoice.LETTER,
    val width: String = "",
    val height: String = "",
    val unit: PageSize.Unit = PageSize.Unit.IN,
) {
    val nameError: String? get() = if (name.isBlank()) "Enter a name" else null
    val widthError: String? get() = if (choice == SizeChoice.CUSTOM) dimensionError(width) else null
    val heightError: String? get() = if (choice == SizeChoice.CUSTOM) dimensionError(height) else null

    val isValid: Boolean get() = nameError == null && widthError == null && heightError == null

    /** The page size to store, or null while the form is invalid. */
    val pageSize: PageSize?
        get() = when (choice) {
            SizeChoice.LETTER -> PageSize.Preset.LETTER
            SizeChoice.A4 -> PageSize.Preset.A4
            SizeChoice.A3 -> PageSize.Preset.A3
            SizeChoice.CUSTOM -> {
                val w = PageSizeValidation.dimension(width)
                val h = PageSizeValidation.dimension(height)
                if (w is PageSizeValidation.Result.Ok && h is PageSizeValidation.Result.Ok) {
                    PageSize.Custom(w.value, h.value, unit)
                } else null
            }
        }

    private fun dimensionError(text: String): String? =
        (PageSizeValidation.dimension(text) as? PageSizeValidation.Result.Error)?.error?.message

    companion object {
        private val dateFormat = DateTimeFormatter.ISO_LOCAL_DATE

        /** A new album: "Album" plus today's date, US Letter. */
        fun forNew(today: LocalDate): AlbumForm = AlbumForm(name = "Album ${today.format(dateFormat)}")

        /**
         * An existing album, prefilled with its current values. A custom size
         * stays custom even when it equals a preset, so nothing changes unless
         * the user changes it.
         */
        fun forEdit(album: Album): AlbumForm {
            val base = AlbumForm(name = album.name)
            return when (val size = PageSize.parse(album.pageSize) ?: PageSize.DEFAULT) {
                PageSize.Preset.LETTER -> base.copy(choice = SizeChoice.LETTER)
                PageSize.Preset.A4 -> base.copy(choice = SizeChoice.A4)
                PageSize.Preset.A3 -> base.copy(choice = SizeChoice.A3)
                is PageSize.Custom -> base.copy(
                    choice = SizeChoice.CUSTOM,
                    width = size.width.stripTrailingZeros().toPlainString(),
                    height = size.height.stripTrailingZeros().toPlainString(),
                    unit = size.unit,
                )
            }
        }

    }
}
