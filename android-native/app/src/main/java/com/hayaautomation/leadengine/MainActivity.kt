package com.hayaautomation.leadengine

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

private const val SUPABASE_URL = "https://jequjltbvofmhfnbsfzt.supabase.co"
private const val FUNCTION_URL = "$SUPABASE_URL/functions/v1/leadengine-brain"
private const val OWNER_EMAIL = "hayaautomation2026@gmail.com"
private const val PUBLIC_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImplcXVqbHRidm9mbWhmbmJzZnp0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODI3NjM3MDYsImV4cCI6MjA5ODMzOTcwNn0.swI7ZldCu4W0fyFNjiOoBMkINEKBfozAVnVJjXj_6VM"

private val Bg = Color(0xFF06100E)
private val Panel = Color(0xFF0D1C18)
private val Green = Color(0xFF48FFA2)
private val Orange = Color(0xFFFF8A3D)
private val Blue = Color(0xFF54BFFF)
private val Purple = Color(0xFFBD80FF)
private val Muted = Color(0xFF829E94)
private val Text = Color(0xFFF4FFF9)
private val ErrorRed = Color(0xFFFF5F7D)

data class Lead(val id: String, val name: String, val phone: String, val city: String, val country: String, val score: Int, val status: String, val reason: String, val notes: String)
data class SearchRequest(val id: String, val title: String, val status: String, val maxLeads: Int)
data class DashboardData(val leads: List<Lead>, val requests: List<SearchRequest>)

private enum class AppPhase { CHECKING, LOGIN, CHANGE_PASSWORD, APP }

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AuthManager.init(applicationContext)
        setContent { LeadEngineRoot() }
    }
}

@Composable
private fun LeadEngineRoot() {
    MaterialTheme(colorScheme = darkColorScheme(background = Bg, surface = Panel, primary = Green)) {
        var phase by remember { mutableStateOf(AppPhase.CHECKING) }
        var authError by remember { mutableStateOf<String?>(null) }
        val scope = rememberCoroutineScope()

        LaunchedEffect(Unit) {
            phase = if (AuthManager.restoreSession()) {
                if (AuthManager.passwordWasChanged()) AppPhase.APP else AppPhase.CHANGE_PASSWORD
            } else AppPhase.LOGIN
        }

        when (phase) {
            AppPhase.CHECKING -> CenterMessage("Securing LeadEngine…")
            AppPhase.LOGIN -> LoginScreen(
                error = authError,
                onLogin = { password ->
                    scope.launch {
                        authError = null
                        runCatching { AuthManager.login(OWNER_EMAIL, password) }
                            .onSuccess { phase = if (AuthManager.passwordWasChanged()) AppPhase.APP else AppPhase.CHANGE_PASSWORD }
                            .onFailure { authError = "Wrong password or connection failed." }
                    }
                }
            )
            AppPhase.CHANGE_PASSWORD -> ChangePasswordScreen(
                error = authError,
                onChange = { password ->
                    scope.launch {
                        authError = null
                        runCatching { AuthManager.changePassword(password) }
                            .onSuccess { phase = AppPhase.APP }
                            .onFailure { authError = "Could not change password. Try again." }
                    }
                }
            )
            AppPhase.APP -> LeadEngineApp(onLogout = {
                AuthManager.logout()
                phase = AppPhase.LOGIN
            })
        }
    }
}

@Composable
private fun CenterMessage(message: String) {
    Box(Modifier.fillMaxSize().background(Bg), contentAlignment = Alignment.Center) {
        Text(message, color = Green, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun LoginScreen(error: String?, onLogin: (String) -> Unit) {
    var password by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    Box(Modifier.fillMaxSize().background(Brush.verticalGradient(listOf(Color(0xFF07110F), Bg))), contentAlignment = Alignment.Center) {
        Card(Modifier.fillMaxWidth().padding(20.dp), colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(24.dp)) {
            Column(Modifier.padding(20.dp)) {
                Text("⚡ LeadEngine", color = Green, fontWeight = FontWeight.Black, fontSize = 28.sp)
                Text("OWNER SIGN-IN", color = Muted, fontSize = 11.sp)
                Spacer(Modifier.height(18.dp))
                OutlinedTextField(value = OWNER_EMAIL, onValueChange = {}, readOnly = true, label = { Text("Owner") }, modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(
                    value = password,
                    onValueChange = { password = it },
                    label = { Text("Password") },
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                error?.let { Text(it, color = ErrorRed, fontSize = 12.sp, modifier = Modifier.padding(top = 8.dp)) }
                Spacer(Modifier.height(14.dp))
                Button(
                    onClick = { if (password.isNotBlank()) { busy = true; onLogin(password) } },
                    enabled = !busy && password.isNotBlank(),
                    modifier = Modifier.fillMaxWidth().height(54.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Green, contentColor = Bg)
                ) { Text("Unlock LeadEngine", fontWeight = FontWeight.Black) }
                Spacer(Modifier.height(8.dp))
                Text("Your session is stored encrypted by Android. The owner password is never saved in the app.", color = Muted, fontSize = 10.sp)
            }
        }
    }
}

@Composable
private fun ChangePasswordScreen(error: String?, onChange: (String) -> Unit) {
    var first by remember { mutableStateOf("") }
    var second by remember { mutableStateOf("") }
    val valid = first.length >= 12 && first == second
    Box(Modifier.fillMaxSize().background(Bg), contentAlignment = Alignment.Center) {
        Card(Modifier.fillMaxWidth().padding(20.dp), colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(24.dp)) {
            Column(Modifier.padding(20.dp)) {
                Text("Create your private password", color = Text, fontWeight = FontWeight.Black, fontSize = 22.sp)
                Text("Do this once. Use at least 12 characters.", color = Muted, fontSize = 11.sp)
                Spacer(Modifier.height(16.dp))
                PasswordField("New password", first) { first = it }
                Spacer(Modifier.height(10.dp))
                PasswordField("Repeat password", second) { second = it }
                if (second.isNotEmpty() && first != second) Text("Passwords do not match", color = ErrorRed, fontSize = 11.sp)
                error?.let { Text(it, color = ErrorRed, fontSize = 11.sp) }
                Spacer(Modifier.height(14.dp))
                Button(onClick = { onChange(first) }, enabled = valid, modifier = Modifier.fillMaxWidth(), colors = ButtonDefaults.buttonColors(containerColor = Green, contentColor = Bg)) {
                    Text("Save private password", fontWeight = FontWeight.Black)
                }
            }
        }
    }
}

@Composable
private fun PasswordField(label: String, value: String, onChange: (String) -> Unit) {
    OutlinedTextField(value = value, onValueChange = onChange, label = { Text(label) }, visualTransformation = PasswordVisualTransformation(), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password), singleLine = true, modifier = Modifier.fillMaxWidth())
}

@Composable
private fun LeadEngineApp(onLogout: () -> Unit) {
    var data by remember { mutableStateOf(DashboardData(emptyList(), emptyList())) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var query by remember { mutableStateOf("") }
    var maxLeads by remember { mutableStateOf("10") }
    var selectedTab by remember { mutableIntStateOf(0) }
    var selectedLead by remember { mutableStateOf<Lead?>(null) }
    val scope = rememberCoroutineScope()

    fun refresh() {
        loading = true
        scope.launch {
            runCatching { Api.dashboard() }
                .onSuccess { data = it; error = null }
                .onFailure { error = it.message ?: "Connection error" }
            loading = false
        }
    }
    LaunchedEffect(Unit) { refresh() }

    Box(Modifier.fillMaxSize().background(Brush.verticalGradient(listOf(Color(0xFF07110F), Bg)))) {
        Column(Modifier.fillMaxSize()) {
            Header(onRefresh = ::refresh, onLogout = onLogout)
            SearchPanel(query, { query = it }, maxLeads, { maxLeads = it }, loading) {
                if (query.isBlank()) return@SearchPanel
                scope.launch {
                    loading = true
                    runCatching { Api.search(query, maxLeads.toIntOrNull() ?: 10) }.onFailure { error = it.message ?: "Search failed" }
                    refresh()
                }
            }
            val hot = data.leads.count { it.score >= 70 && it.status == "new" }
            val converted = data.leads.count { it.status == "converted" }
            val working = data.requests.count { it.status in listOf("pending", "processing", "running") }
            KpiRow(hot, working, converted, data.leads.size)
            Row(Modifier.padding(horizontal = 14.dp)) {
                TabButton("Leads", selectedTab == 0) { selectedTab = 0 }
                Spacer(Modifier.width(8.dp)); TabButton("Queue", selectedTab == 1) { selectedTab = 1 }
            }
            error?.let { Text(it, color = ErrorRed, fontSize = 12.sp, modifier = Modifier.padding(14.dp)) }
            if (selectedTab == 0) LeadList(data.leads) { selectedLead = it } else QueueList(data.requests)
        }
        selectedLead?.let { lead -> LeadDialog(lead, { selectedLead = null }) { selectedLead = null; refresh() } }
    }
}

@Composable
private fun Header(onRefresh: () -> Unit, onLogout: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween) {
        Column { Text("⚡ LeadEngine", color = Green, fontWeight = FontWeight.Black, fontSize = 25.sp); Text("Private owner console", color = Muted, fontSize = 11.sp) }
        Row { OutlinedButton(onClick = onRefresh) { Text("↻", color = Green) }; Spacer(Modifier.width(6.dp)); TextButton(onClick = onLogout) { Text("Lock", color = Muted) } }
    }
}

@Composable
private fun SearchPanel(query: String, onQueryChange: (String) -> Unit, maxLeads: String, onMaxLeadsChange: (String) -> Unit, loading: Boolean, onSearch: () -> Unit) {
    Card(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 4.dp), colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(22.dp)) {
        Column(Modifier.padding(16.dp)) {
            Text("LIVE SEARCH", color = Green, fontSize = 10.sp, fontWeight = FontWeight.Bold)
            Text("What businesses do you want?", color = Text, fontSize = 18.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(10.dp))
            OutlinedTextField(value = query, onValueChange = onQueryChange, label = { Text("e.g. Dentists in Utah") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
            Spacer(Modifier.height(8.dp))
            Row {
                OutlinedTextField(value = maxLeads, onValueChange = { if (it.all(Char::isDigit)) onMaxLeadsChange(it.take(2)) }, label = { Text("Leads") }, modifier = Modifier.width(100.dp), singleLine = true)
                Spacer(Modifier.width(8.dp))
                Button(onClick = onSearch, enabled = !loading, modifier = Modifier.weight(1f).height(56.dp), colors = ButtonDefaults.buttonColors(containerColor = Green, contentColor = Bg)) { Text(if (loading) "Working…" else "⚡ Run Search", fontWeight = FontWeight.Black) }
            }
        }
    }
}

@Composable
private fun KpiRow(hot: Int, working: Int, converted: Int, total: Int) {
    Row(Modifier.fillMaxWidth().padding(14.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        KpiCard("🔥 Hot", hot, Orange, Modifier.weight(1f)); KpiCard("⚡ Queue", working, Blue, Modifier.weight(1f)); KpiCard("✓ Won", converted, Green, Modifier.weight(1f)); KpiCard("◎ Total", total, Purple, Modifier.weight(1f))
    }
}

@Composable private fun KpiCard(label: String, number: Int, color: Color, modifier: Modifier) { Card(modifier, colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(16.dp)) { Column(Modifier.padding(10.dp)) { Text(label, color = Muted, fontSize = 9.sp); Text(number.toString(), color = color, fontSize = 24.sp, fontWeight = FontWeight.Black) } } }
@Composable private fun TabButton(text: String, selected: Boolean, onClick: () -> Unit) { Button(onClick = onClick, colors = ButtonDefaults.buttonColors(containerColor = if (selected) Green else Panel, contentColor = if (selected) Bg else Text), shape = RoundedCornerShape(14.dp)) { Text(text, fontWeight = FontWeight.Bold) } }

@Composable
private fun LeadList(leads: List<Lead>, onOpen: (Lead) -> Unit) {
    if (leads.isEmpty()) { Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("No leads yet", color = Muted) }; return }
    LazyColumn(contentPadding = PaddingValues(14.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
        items(leads, key = { it.id }) { lead ->
            Card(Modifier.fillMaxWidth().clickable { onOpen(lead) }, colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(18.dp)) {
                Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) { Text(lead.name, color = Text, fontWeight = FontWeight.Bold, fontSize = 14.sp); val location = listOf(lead.city, lead.country).filter { it.isNotBlank() }.joinToString(" · "); if (location.isNotBlank()) Text(location, color = Muted, fontSize = 10.sp); if (lead.reason.isNotBlank()) Text(lead.reason, color = Muted, fontSize = 9.sp, maxLines = 2) }
                    Text(lead.score.toString(), color = if (lead.score >= 70) Orange else Green, fontWeight = FontWeight.Black, fontSize = 18.sp); Spacer(Modifier.width(10.dp)); AssistChip(onClick = { onOpen(lead) }, label = { Text(lead.status.replace('_', ' '), fontSize = 9.sp) })
                }
            }
        }
    }
}

@Composable
private fun QueueList(requests: List<SearchRequest>) {
    if (requests.isEmpty()) { Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("Queue is clear", color = Muted) }; return }
    LazyColumn(contentPadding = PaddingValues(14.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
        items(requests, key = { it.id }) { r -> Card(colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(16.dp)) { Row(Modifier.fillMaxWidth().padding(14.dp), horizontalArrangement = Arrangement.SpaceBetween) { Column(Modifier.weight(1f)) { Text(r.title, color = Text, fontWeight = FontWeight.Bold); Text("Up to ${r.maxLeads} leads", color = Muted, fontSize = 10.sp) }; Text(r.status, color = if (r.status == "completed") Green else Blue, fontSize = 11.sp, fontWeight = FontWeight.Bold) } } }
    }
}

@Composable
private fun LeadDialog(lead: Lead, onDismiss: () -> Unit, onSaved: () -> Unit) {
    val context = LocalContext.current; var status by remember(lead.id) { mutableStateOf(lead.status) }; var notes by remember(lead.id) { mutableStateOf(lead.notes) }; var saving by remember(lead.id) { mutableStateOf(false) }; val scope = rememberCoroutineScope(); val statuses = listOf("new", "contacted", "qualified", "converted", "cold", "no_reply")
    AlertDialog(onDismissRequest = onDismiss, title = { Text(lead.name) }, text = { Column { if (lead.phone.isNotBlank()) { Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) { Button(onClick = { openDialer(context, lead.phone) }) { Text("☎ Call") }; Button(onClick = { openWhatsAppOnly(context, lead.phone) }) { Text("💬 WhatsApp") } }; Spacer(Modifier.height(10.dp)) }; Text("Status", color = Muted, fontSize = 11.sp); statuses.chunked(2).forEach { row -> Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) { row.forEach { s -> FilterChip(selected = status == s, onClick = { status = s }, label = { Text(s.replace('_', ' '), fontSize = 9.sp) }) } } }; OutlinedTextField(value = notes, onValueChange = { notes = it }, label = { Text("Notes") }, modifier = Modifier.fillMaxWidth()) } }, confirmButton = { Button(enabled = !saving, onClick = { saving = true; scope.launch { runCatching { Api.updateLead(lead.id, status, notes) }; saving = false; onSaved() } }) { Text(if (saving) "Saving…" else "Save") } }, dismissButton = { TextButton(onClick = onDismiss) { Text("Close") } })
}

private fun openDialer(context: Context, phone: String) { runCatching { context.startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:${Uri.encode(phone)}"))) }.onFailure { Toast.makeText(context, "Phone app unavailable", Toast.LENGTH_SHORT).show() } }
private fun openWhatsAppOnly(context: Context, phone: String) { val uri = Uri.parse("whatsapp://send?phone=${phone.filter(Char::isDigit)}"); try { context.startActivity(Intent(Intent.ACTION_VIEW, uri).apply { setPackage("com.whatsapp") }); return } catch (_: ActivityNotFoundException) {}; try { context.startActivity(Intent(Intent.ACTION_VIEW, uri).apply { setPackage("com.whatsapp.w4b") }) } catch (_: ActivityNotFoundException) { Toast.makeText(context, "WhatsApp is not installed", Toast.LENGTH_SHORT).show() } }

private object AuthManager {
    private const val PREFS = "leadengine_secure"
    private const val KEY_ALIAS = "leadengine_session_key"
    private lateinit var context: Context
    private var accessToken: String? = null
    private var refreshToken: String? = null

    fun init(appContext: Context) { context = appContext.applicationContext }
    fun passwordWasChanged(): Boolean = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("password_changed", false)

    suspend fun login(email: String, password: String) = withContext(Dispatchers.IO) {
        val body = JSONObject().put("email", email).put("password", password)
        val json = authRequest("$SUPABASE_URL/auth/v1/token?grant_type=password", "POST", body, null)
        acceptSession(json)
    }

    suspend fun restoreSession(): Boolean = withContext(Dispatchers.IO) {
        val encrypted = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("refresh_token", null) ?: return@withContext false
        val token = runCatching { decrypt(encrypted) }.getOrNull() ?: return@withContext false
        refreshToken = token
        runCatching { refresh() }.isSuccess
    }

    suspend fun ensureAccessToken(): String {
        accessToken?.let { return it }
        refresh()
        return accessToken ?: throw IllegalStateException("Login required")
    }

    suspend fun refresh() = withContext(Dispatchers.IO) {
        val token = refreshToken ?: throw IllegalStateException("Login required")
        val json = authRequest("$SUPABASE_URL/auth/v1/token?grant_type=refresh_token", "POST", JSONObject().put("refresh_token", token), null)
        acceptSession(json)
    }

    suspend fun changePassword(newPassword: String) = withContext(Dispatchers.IO) {
        require(newPassword.length >= 12)
        val token = ensureAccessToken()
        authRequest("$SUPABASE_URL/auth/v1/user", "PUT", JSONObject().put("password", newPassword), token)
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("password_changed", true).apply()
    }

    fun logout() {
        accessToken = null; refreshToken = null
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove("refresh_token").apply()
    }

    private fun acceptSession(json: JSONObject) {
        accessToken = json.getString("access_token")
        refreshToken = json.getString("refresh_token")
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("refresh_token", encrypt(refreshToken!!)).apply()
    }

    private fun authRequest(url: String, method: String, body: JSONObject, bearer: String?): JSONObject {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply { requestMethod = method; connectTimeout = 15000; readTimeout = 30000; doOutput = true; setRequestProperty("Content-Type", "application/json"); setRequestProperty("apikey", PUBLIC_KEY); if (bearer != null) setRequestProperty("Authorization", "Bearer $bearer") }
        try { conn.outputStream.use { it.write(body.toString().toByteArray()) }; val code = conn.responseCode; val text = (if (code in 200..299) conn.inputStream else conn.errorStream)?.bufferedReader()?.use { it.readText() }.orEmpty(); if (code !in 200..299) throw IllegalStateException("Auth failed"); return JSONObject(text) } finally { conn.disconnect() }
    }

    private fun secretKey(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        return generator.generateKey()
    }

    private fun encrypt(value: String): String { val cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE, secretKey()); return Base64.encodeToString(cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8)), Base64.NO_WRAP) }
    private fun decrypt(value: String): String { val bytes = Base64.decode(value, Base64.NO_WRAP); val iv = bytes.copyOfRange(0, 12); val encrypted = bytes.copyOfRange(12, bytes.size); val cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(128, iv)); return String(cipher.doFinal(encrypted), Charsets.UTF_8) }
}

private object Api {
    suspend fun dashboard(): DashboardData = withContext(Dispatchers.IO) {
        val json = post(JSONObject().put("action", "dashboard")); val leadsJson = json.optJSONArray("leads") ?: JSONArray(); val requestsJson = json.optJSONArray("requests") ?: JSONArray()
        val leads = buildList { for (i in 0 until leadsJson.length()) { val x = leadsJson.getJSONObject(i); add(Lead(x.optString("id"), x.optString("full_name").ifBlank { x.optString("agency_name", "Unknown") }, x.optString("whatsapp_number").ifBlank { x.optString("phone_number") }, x.optString("city"), x.optString("country"), x.optInt("pain_score"), x.optString("contact_status", "new"), x.optString("pain_reason"), x.optString("notes"))) } }
        val requests = buildList { for (i in 0 until requestsJson.length()) { val x = requestsJson.getJSONObject(i); val title = listOf(x.optString("query"), x.optString("location")).filter { it.isNotBlank() }.joinToString(" in "); add(SearchRequest(x.optString("id"), title, x.optString("status"), x.optInt("max_leads", 20))) } }
        DashboardData(leads, requests)
    }
    suspend fun search(input: String, maxLeads: Int) = withContext(Dispatchers.IO) { val match = Regex("^(.*?)\\s+in\\s+(.+)$", RegexOption.IGNORE_CASE).find(input.trim()); val body = JSONObject().put("action", "search").put("max_leads", maxLeads.coerceIn(1, 50)); if (match != null) { body.put("query", match.groupValues[1].trim()); body.put("location", match.groupValues[2].trim()) } else body.put("query", input.trim()); post(body) }
    suspend fun updateLead(id: String, status: String, notes: String) = withContext(Dispatchers.IO) { post(JSONObject().put("action", "update_lead").put("id", id).put("contact_status", status).put("notes", notes)) }

    private suspend fun post(body: JSONObject): JSONObject {
        var token = AuthManager.ensureAccessToken()
        var result = rawPost(body, token)
        if (result.first == 401 || result.first == 403) { AuthManager.refresh(); token = AuthManager.ensureAccessToken(); result = rawPost(body, token) }
        if (result.first !in 200..299) throw IllegalStateException(if (result.first == 429) "Too many searches. Try again later." else "LeadEngine server error ${result.first}")
        return if (result.second.isBlank()) JSONObject() else JSONObject(result.second)
    }

    private fun rawPost(body: JSONObject, token: String): Pair<Int, String> {
        val connection = (URL(FUNCTION_URL).openConnection() as HttpURLConnection).apply { requestMethod = "POST"; connectTimeout = 15000; readTimeout = 30000; doOutput = true; setRequestProperty("Content-Type", "application/json"); setRequestProperty("apikey", PUBLIC_KEY); setRequestProperty("Authorization", "Bearer $token") }
        return try { connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }; val code = connection.responseCode; val text = (if (code in 200..299) connection.inputStream else connection.errorStream)?.bufferedReader()?.use { it.readText() }.orEmpty(); code to text } finally { connection.disconnect() }
    }
}
