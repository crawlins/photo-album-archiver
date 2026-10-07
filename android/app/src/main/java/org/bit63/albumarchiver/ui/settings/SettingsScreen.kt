package org.bit63.albumarchiver.ui.settings

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import org.bit63.albumarchiver.ui.LocalContainer
import org.bit63.albumarchiver.ui.rememberLocalNetworkAccess

/** Server URL, access token, upload network and "Test connection" (Requirement 9). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(back: () -> Unit) {
    val container = LocalContainer.current
    val vm: SettingsViewModel = viewModel {
        SettingsViewModel(container.settings, { container.rescheduleUploads() })
    }
    val form by vm.form.collectAsStateWithLifecycle()
    val withLocalNetwork = rememberLocalNetworkAccess()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Settings") },
                navigationIcon = {
                    IconButton(onClick = back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back") }
                },
            )
        },
    ) { padding ->
        Column(
            Modifier.fillMaxSize().padding(padding).padding(16.dp).verticalScroll(rememberScrollState()),
        ) {
            Text("Server", style = MaterialTheme.typography.titleMedium)
            OutlinedTextField(
                value = form.url,
                onValueChange = vm::setUrl,
                label = { Text("Server URL") },
                placeholder = { Text("https://archive.example:8080") },
                singleLine = true,
                isError = form.urlError != null,
                supportingText = {
                    when {
                        form.urlError != null -> Text(form.urlError!!)
                        form.cleartextWarning -> Text(
                            "With http, the token and photos travel unencrypted.",
                            color = MaterialTheme.colorScheme.error,
                            modifier = Modifier.testTag("httpWarning"),
                        )
                    }
                },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.fillMaxWidth().testTag("serverUrl"),
            )
            OutlinedTextField(
                value = form.token,
                onValueChange = vm::setToken,
                label = { Text("Access token") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                modifier = Modifier.fillMaxWidth().testTag("token"),
            )
            Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Button(onClick = { withLocalNetwork(vm::save) }, enabled = form.urlError == null, modifier = Modifier.testTag("save")) {
                    Text("Save")
                }
                OutlinedButton(
                    onClick = { withLocalNetwork(vm::testConnection) },
                    enabled = form.canTest,
                    modifier = Modifier.padding(start = 8.dp).testTag("testConnection"),
                ) { Text(if (form.testing) "Testing…" else "Test connection") }
            }
            if (form.saved) Text("Saved.", modifier = Modifier.padding(top = 4.dp))
            form.result?.let {
                Text(
                    it.message,
                    color = if (it == ConnectionResult.OK) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(top = 4.dp).testTag("connectionResult"),
                )
            }

            Text("Uploads", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 24.dp))
            NetworkOption("Only on unmetered networks (Wi-Fi)", form.unmeteredOnly) { vm.setUnmeteredOnly(true) }
            NetworkOption("On any network, including mobile data", !form.unmeteredOnly) { vm.setUnmeteredOnly(false) }
        }
    }
}

@Composable
private fun NetworkOption(label: String, selected: Boolean, onSelect: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().selectable(selected = selected, onClick = onSelect).padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = selected, onClick = null)
        Text(label, modifier = Modifier.padding(start = 8.dp))
    }
}
