package org.bit63.albumarchiver.data

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class FeedbackTest {
    private val single = """
        {"code":"single_coverage","fraction":0.31,"locations":["right"],
         "regions":[{"bbox":[0.62,0,1,1],"location":"right","shots":[1],"shot_boxes":{"s2":[2210,0,4079,3071]}}],
         "message":"Only one photo covers the right edge (31.0% of the page). Take another shot of that part from a different angle so that glare there can be removed."}
    """.trimIndent()
    private val glare = """
        {"code":"glare","fraction":0.04,"single_shot":0,"all_shots_glared":1,"location":"centre",
         "regions":[{"bbox":[0.4,0.4,0.6,0.6],"location":"centre","shots":[0,1],"cause":"all_shots_glared",
                     "shot_boxes":{"s1":[100,200,300,400],"s2":[150,250,350,450]}}],
         "message":"Glare remains on 4.0% of the page in 1 area (centre). Every photo had glare there; change the light or the camera angle for all the shots."}
    """.trimIndent()
    private val uncovered = """{"code":"incomplete_coverage","fraction":0.01,"locations":["top-left"],"regions":[{"bbox":[0,0,0.1,0.1],"location":"top-left","edges":["top","left"],"shot_boxes":{}}],"message":"1.0% of the page is not in any photo (top-left corner)."}"""
    private val dropped = """{"code":"photos_dropped","photos":[2],"message":"Photo 2 matched no other photo and was not used."}"""

    @Test fun `warnings parse with their regions and shot boxes keyed by shot id`() {
        val w = parseWarnings("[$single,$glare,$dropped]")
        assertThat(w.map { it.code }).containsExactly("single_coverage", "glare", "photos_dropped").inOrder()
        val r = w[0].regions.single()
        assertThat(r.location).isEqualTo("right")
        assertThat(r.shots).containsExactly(1)
        assertThat(r.shotBoxes).containsExactly("s2", listOf(2210, 0, 4079, 3071))
        assertThat(w[1].regions.single().shotBoxes.keys).containsExactly("s1", "s2")
        assertThat(w[2].regions).isEmpty()
    }

    @Test fun `older or odd warnings are kept with what they have`() {
        // Schema 2 gave `regions` as a count; a warning without a code is skipped; a box with three numbers is not a box.
        val w = parseWarnings("""[{"code":"glare","regions":2,"message":"m"},{"message":"no code"},{"code":"x","regions":[{"shot_boxes":{"a":[1,2,3]}}]}]""")
        assertThat(w.map { it.code }).containsExactly("glare", "x").inOrder()
        assertThat(w[0].regions).isEmpty()
        assertThat(w[1].regions.single().shotBoxes).isEmpty()
        assertThat(parseWarnings("not json")).isEmpty()
        assertThat(parseWarnings("{}")).isEmpty()
    }

    @Test fun `only area warnings mark a page`() {
        assertThat(parseWarnings("[$dropped]").marksPage()).isFalse()
        assertThat(parseWarnings("[$dropped,$uncovered]").marksPage()).isTrue()
        assertThat(parseWarnings("[$single]").marksPage()).isTrue()
        assertThat(emptyList<PageWarning>().marksPage()).isFalse()
    }

    @Test fun `rectangles are the shot's boxes as fractions of the photo`() {
        val w = parseWarnings("[$single,$glare,$uncovered]")
        val s2 = w.rectsFor("s2", 4080, 3072)
        assertThat(s2.map { it.code }).containsExactly("single_coverage", "glare").inOrder()
        val box = s2[0]
        assertThat(box.left).isWithin(1e-4f).of(2210f / 4080)
        assertThat(box.top).isEqualTo(0f)
        assertThat(box.right).isEqualTo(1f)
        assertThat(box.bottom).isEqualTo(1f)
        assertThat(w.rectsFor("s1", 4080, 3072).single().code).isEqualTo("glare")
        assertThat(w.rectsFor("s9", 4080, 3072)).isEmpty()
        assertThat(w.rectsFor("s2", 0, 0)).isEmpty()
    }

    @Test fun `the bar names where to look when the photo does not show the area`() {
        val w = parseWarnings("[$single,$glare,$dropped]")
        val onS1 = w.linesFor("s1")
        assertThat(onS1).hasSize(3)
        assertThat(onS1[0].text).endsWith("Not in this photo; look at the right.")
        assertThat(onS1[1].text).isEqualTo(w[1].message)  // the glare region is in s1
        assertThat(onS1[2].text).isEqualTo(w[2].message)  // not an area warning: as is
        assertThat(w.linesFor("s2")[0].text).isEqualTo(w[0].message)
        assertThat(w.linesFor(null)[0].text).endsWith("look at the right.")
    }

    @Test fun `the first shot with a box opens the review`() {
        val w = parseWarnings("[$single,$glare]")
        assertThat(w.firstShotWithBox(listOf("s1", "s2", "s3"))).isEqualTo("s1")
        assertThat(w.firstShotWithBox(listOf("s3", "s2"))).isEqualTo("s2")
        assertThat(w.firstShotWithBox(listOf("s3"))).isNull()
        assertThat(parseWarnings("[$dropped]").firstShotWithBox(listOf("s1"))).isNull()
    }
}
