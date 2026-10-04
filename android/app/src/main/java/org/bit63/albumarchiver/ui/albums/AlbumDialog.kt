package org.bit63.albumarchiver.ui.albums

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import org.bit63.albumarchiver.data.PageSize

/**
 * One form for creating and editing an album (Requirements 2.3 to 2.5, 2.8):
 * a name and a page size of 8.5 × 11 in, A4, A3 or custom. Problems are shown
 * next to the field, and the confirm button stays disabled until there are none.
 */
@Composable
fun AlbumDialog(
    title: String,
    confirmLabel: String,
    initial: AlbumForm,
    onConfirm: (AlbumForm) -> Unit,
    onDismiss: () -> Unit,
) {
    var form by remember { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                OutlinedTextField(
                    value = form.name,
                    onValueChange = { form = form.copy(name = it) },
                    label = { Text("Name") },
                    singleLine = true,
                    isError = form.nameError != null,
                    supportingText = { form.nameError?.let { Text(it) } },
                    modifier = Modifier.fillMaxWidth().testTag("albumNameField"),
                )
                Text("Page size", style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(top = 8.dp))
                SizeOption("8.5 × 11 in", form.choice == SizeChoice.LETTER) { form = form.copy(choice = SizeChoice.LETTER) }
                SizeOption("A4", form.choice == SizeChoice.A4) { form = form.copy(choice = SizeChoice.A4) }
                SizeOption("A3", form.choice == SizeChoice.A3) { form = form.copy(choice = SizeChoice.A3) }
                SizeOption("Custom", form.choice == SizeChoice.CUSTOM) { form = form.copy(choice = SizeChoice.CUSTOM) }
                if (form.choice == SizeChoice.CUSTOM) {
                    Row {
                        OutlinedTextField(
                            value = form.width,
                            onValueChange = { form = form.copy(width = it) },
                            label = { Text("Width") },
                            singleLine = true,
                            isError = form.widthError != null,
                            supportingText = { form.widthError?.let { Text(it) } },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                            modifier = Modifier.weight(1f).testTag("width"),
                        )
                        Spacer(Modifier.width(8.dp))
                        OutlinedTextField(
                            value = form.height,
                            onValueChange = { form = form.copy(height = it) },
                            label = { Text("Height") },
                            singleLine = true,
                            isError = form.heightError != null,
                            supportingText = { form.heightError?.let { Text(it) } },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                            modifier = Modifier.weight(1f).testTag("height"),
                        )
                    }
                    Row {
                        PageSize.Unit.entries.forEach { unit ->
                            FilterChip(
                                selected = form.unit == unit,
                                onClick = { form = form.copy(unit = unit) },
                                label = { Text(unit.label) },
                                modifier = Modifier.padding(end = 8.dp),
                            )
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onConfirm(form) },
                enabled = form.isValid,
                modifier = Modifier.testTag("confirmAlbum"),
            ) { Text(confirmLabel) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun SizeOption(label: String, selected: Boolean, onSelect: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().selectable(selected = selected, onClick = onSelect).padding(vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = selected, onClick = null)
        Text(label, modifier = Modifier.padding(start = 8.dp))
    }
}
