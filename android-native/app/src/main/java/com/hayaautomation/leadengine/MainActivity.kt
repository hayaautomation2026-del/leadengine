package com.hayaautomation.leadengine

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

private const val FUNCTION_URL = "https://jequjltbvofmhfnbsfzt.supabase.co/functions/v1/leadengine-brain"
private const val PUBLIC_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImplcXVqbHRidm9mbWhmbmJzZnp0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODI3NjM3MDYsImV4cCI6MjA5ODMzOTcwNn0.swI7ZldCu4W0fyFNjiOoBMkINEKBfozAVnVJjXj_6VM"

private val Bg = Color(0xFF06100E)
private val Panel = Color(0xFF0D1C18)
private val Green = Color(0xFF48FFA2)
private val Orange = Color(0xFFFF8A3D)
private val Blue = Color(0xFF54BFFF)
private val Purple = Color(0xFFBD80FF)
private val Muted = Color(0xFF829E94)
private val Text = Color(0xFFF4FFF9)

data class Lead(
    val id: String,
    val name: String,
    val phone: String,
    val city: String,
    val country: String,
    val score: Int,
    val status: String,
    val reason: String,
    val notes: String
)

data class SearchRequest(val id: String, val title: String, val status: String, val maxLeads: Int)

data class DashboardData(val leads: List<Lead>, val requests: List<SearchRequest>)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { LeadEngineApp() }
    }
}

@Composable
fun LeadEngineApp() {
    MaterialTheme(colorScheme = darkColorScheme(background = Bg, surface = Panel, primary = Green)) {
        var data by remember { mutableStateOf(DashboardData(emptyList(), emptyList())) }
        var loading by remember { mutableStateOf(true) }
        var error by remember { mutableStateOf<String?>(null) }
        var query by remember { mutableStateOf("") }
        var maxLeads by remember { mutableStateOf("10") }
        var tab by remember { mutableIntStateOf(0) }
        var selectedLead by remember { mutableStateOf<Lead?>(null) }
        val scope = remember { CoroutineScope(Dispatchers.Main) }

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

        Box(
            Modifier
                .fillMaxSize()
                .background(Brush.verticalGradient(listOf(Color(0xFF07110F), Bg)))
        ) {
            Column(Modifier.fillMaxSize()) {
                Header(onRefresh = { refresh() })
                SearchBox(
                    query = query,
                    onQuery = { query = it },
                    maxLeads = maxLeads,
                    onMaxLeads = { maxLeads = it },
                    loading = loading,
                    onSearch = {
                        if (query.isBlank()) return@SearchBox
                        scope.launch {
                            loading = true
                            runCatching { Api.search(query, maxLeads.toIntOrNull() ?: 10) }
                                .onFailure { error = it.message }
                            refresh()
                        }
                    }
                )

                val hot = data.leads.count { it.score >= 70 && it.status == "new" }
                val converted = data.leads.count { it.status == "converted" }
                val working = data.requests.count { it.status in listOf("pending", "processing", "running") }
                Kpis(hot, working, converted, data.leads.size)

                Row(Modifier.padding(horizontal = 14.dp)) {
                    TabButton("Leads", tab == 0) { tab = 0 }
                    Spacer(Modifier.width(8.dp))
                    TabButton("Queue", tab == 1) { tab = 1 }
                }

                if (error != null) {
                    Text(error!!, color = Color(0xFFFF5F7D), fontSize = 12.sp, modifier = Modifier.padding(14.dp))
                }

                if (tab == 0) {
                    LeadList(data.leads, onOpen = { selectedLead = it })
                } else {
                    QueueList(data.requests)
                }
            }

            if (selectedLead != null) {
                LeadSheet(
                    lead = selectedLead!!,
                    onDismiss = { selectedLead = null },
                    onSaved = {
                        selectedLead = null
                        refresh()
                    }
                )
            }
        }
    }
}

@Composable
private fun Header(onRefresh: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(16.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Column {
            Text("⚡ LeadEngine", color = Green, fontWeight = FontWeight.Black, fontSize = 25.sp)
            Text("Your pocket lead command center", color = Muted, fontSize = 11.sp)
        }
        OutlinedButton(onClick = onRefresh) { Text("↻", color = Green, fontSize = 20.sp) }
    }
}

@Composable
private fun SearchBox(
    query: String,
    onQuery: (String) -> Unit,
    maxLeads: String,
    onMaxLeads: (String) -> Unit,
    loading: Boolean,
    onSearch: () -> Unit
) {
    Card(
        Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 4.dp),
        colors = CardDefaults.cardColors(containerColor = Panel),
        shape = RoundedCornerShape(22.dp)
    ) {
        Column(Modifier.padding(16.dp)) {
            Text("LIVE SEARCH", color = Green, fontSize = 10.sp, fontWeight = FontWeight.Bold)
            Text("What businesses do you want?", color = Text, fontSize = 18.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(10.dp))
            OutlinedTextField(
                value = query,
                onValueChange = onQuery,
                label = { Text("e.g. Dentists in Utah") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true
            )
            Spacer(Modifier.height(8.dp))
            Row {
                OutlinedTextField(
                    value = maxLeads,
                    onValueChange = { if (it.all(Char::isDigit)) onMaxLeads(it.take(2)) },
                    label = { Text("Leads") },
                    modifier = Modifier.width(100.dp),
                    singleLine = true
                )
                Spacer(Modifier.width(8.dp))
                Button(
                    onClick = onSearch,
                    enabled = !loading,
                    modifier = Modifier.weight(1f).height(56.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Green, contentColor = Bg)
                ) { Text(if (loading) "Working…" else "⚡ Run Search", fontWeight = FontWeight.Black) }
            }
        }
    }
}

@Composable
private fun Kpis(hot: Int, working: Int, converted: Int, total: Int) {
    Row(Modifier.fillMaxWidth().padding(14.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Kpi("🔥 Hot", hot, Orange, Modifier.weight(1f))
        Kpi("⚡ Queue", working, Blue, Modifier.weight(1f))
        Kpi("✓ Won", converted, Green, Modifier.weight(1f))
        Kpi("◎ Total", total, Purple, Modifier.weight(1f))
    }
}

@Composable
private fun Kpi(label: String, number: Int, color: Color, modifier: Modifier) {
    Card(modifier, colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(16.dp)) {
        Column(Modifier.padding(10.dp)) {
            Text(label, color = Muted, fontSize = 9.sp)
            Text(number.toString(), color = color, fontSize = 24.sp, fontWeight = FontWeight.Black)
        }
    }
}

@Composable
private fun TabButton(text: String, selected: Boolean, onClick: () -> Unit) {
    Button(
        onClick = onClick,
        colors = ButtonDefaults.buttonColors(containerColor = if (selected) Green else Panel, contentColor = if (selected) Bg else Text),
        shape = RoundedCornerShape(14.dp)
    ) { Text(text, fontWeight = FontWeight.Bold) }
}

@Composable
private fun LeadList(leads: List<Lead>, onOpen: (Lead) -> Unit) {
    if (leads.isEmpty()) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("No leads yet", color = Muted) }
        return
    }
    LazyColumn(contentPadding = PaddingValues(14.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
        items(leads, key = { it.id }) { lead ->
            Card(
                Modifier.fillMaxWidth().clickable { onOpen(lead) },
                colors = CardDefaults.cardColors(containerColor = Panel),
                shape = RoundedCornerShape(18.dp)
            ) {
                Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(lead.name, color = Text, fontWeight = FontWeight.Bold, fontSize = 14.sp)
                        Text(listOf(lead.city, lead.country).filter { it.isNotBlank() }.joinToString(" · "), color = Muted, fontSize = 10.sp)
                        if (lead.reason.isNotBlank()) Text(lead.reason, color = Muted, fontSize = 9.sp, maxLines = 2)
                    }
                    Text(lead.score.toString(), color = if (lead.score >= 70) Orange else Green, fontWeight = FontWeight.Black, fontSize = 18.sp)
                    Spacer(Modifier.width(10.dp))
                    AssistChip(onClick = { onOpen(lead) }, label = { Text(lead.status.replace('_', ' '), fontSize = 9.sp) })
                }
            }
        }
    }
}

@Composable
private fun QueueList(requests: List<SearchRequest>) {
    if (requests.isEmpty()) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("Queue is clear", color = Muted) }
        return
    }
    LazyColumn(contentPadding = PaddingValues(14.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
        items(requests, key = { it.id }) { r ->
            Card(colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(16.dp)) {
                Row(Modifier.fillMaxWidth().padding(14.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                    Column(Modifier.weight(1f)) {
                        Text(r.title, color = Text, fontWeight = FontWeight.Bold)
                        Text("Up to ${r.maxLeads} leads", color = Muted, fontSize = 10.sp)
                    }
                    Text(r.status, color = if (r.status == "completed") Green else Blue, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}

@Composable
private fun LeadSheet(lead: Lead, onDismiss: () -> Unit, onSaved: () -> Unit) {
    val context = LocalContext.current
    var status by remember(lead.id) { mutableStateOf(lead.status) }
    var notes by remember(lead.id) { mutableStateOf(lead.notes) }
    var saving by remember { mutableStateOf(false) }
    val scope = remember { CoroutineScope(Dispatchers.Main) }
    val statuses = listOf("new", "contacted", "qualified", "converted", "cold", "no_reply")

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(lead.name) },
        text = {
            Column {
                if (lead.phone.isNotBlank()) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { context.startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:${lead.phone}"))) }) { Text("☎ Call") }
                        Button(onClick = { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://wa.me/${lead.phone.filter(Char::isDigit)}"))) }) { Text("💬 WhatsApp") }
                    }
                    Spacer(Modifier.height(10.dp))
                }
                Text("Status", color = Muted, fontSize = 11.sp)
                statuses.chunked(2).forEach { row ->
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        row.forEach { s ->
                            FilterChip(selected = status == s, onClick = { status = s }, label = { Text(s.replace('_', ' '), fontSize = 9.sp) })
                        }
                    }
                }
                OutlinedTextField(value = notes, onValueChange = { notes = it }, label = { Text("Notes") }, modifier = Modifier.fillMaxWidth())
            }
        },
        confirmButton = {
            Button(enabled = !saving, onClick = {
                saving = true
                scope.launch {
                    runCatching { Api.updateLead(lead.id, status, notes) }
                    saving = false
                    onSaved()
                }
            }) { Text(if (saving) "Saving…" else "Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Close") } }
    )
}

private object Api {
    suspend fun dashboard(): DashboardData = withContext(Dispatchers.IO) {
        val json = post(JSONObject().put("action", "dashboard"))
        val leadsJson = json.optJSONArray("leads") ?: JSONArray()
        val reqJson = json.optJSONArray("requests") ?: JSONArray()
        val leads = buildList {
            for (i in 0 until leadsJson.length()) {
                val x = leadsJson.getJSONObject(i)
                add(
                    Lead(
                        id = x.optString("id"),
                        name = x.optString("full_name").ifBlank { x.optString("agency_name", "Unknown") },
                        phone = x.optString("whatsapp_number").ifBlank { x.optString("phone_number") },
                        city = x.optString("city"),
                        country = x.optString("country"),
                        score = x.optInt("pain_score"),
                        status = x.optString("contact_status", "new"),
                        reason = x.optString("pain_reason"),
                        notes = x.optString("notes")
                    )
                )
            }
        }
        val requests = buildList {
            for (i in 0 until reqJson.length()) {
                val x = reqJson.getJSONObject(i)
                val title = listOf(x.optString("query"), x.optString("location")).filter { it.isNotBlank() }.joinToString(" in ")
                add(SearchRequest(x.optString("id"), title, x.optString("status"), x.optInt("max_leads", 20)))
            }
        }
        DashboardData(leads, requests)
    }

    suspend fun search(input: String, maxLeads: Int) = withContext(Dispatchers.IO) {
        val match = Regex("^(.*?)\\s+in\\s+(.+)$", RegexOption.IGNORE_CASE).find(input.trim())
        val body = JSONObject().put("action", "search").put("max_leads", maxLeads.coerceIn(1, 100))
        if (match != null) {
            body.put("query", match.groupValues[1].trim())
            body.put("location", match.groupValues[2].trim())
        } else body.put("query", input.trim())
        post(body)
    }

    suspend fun updateLead(id: String, status: String, notes: String) = withContext(Dispatchers.IO) {
        post(JSONObject().put("action", "update_lead").put("id", id).put("contact_status", status).put("notes", notes))
    }

    private fun post(body: JSONObject): JSONObject {
        val conn = URL(FUNCTION_URL).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = "POST"
            conn.connectTimeout = 15000
            conn.readTimeout = 30000
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("apikey", PUBLIC_KEY)
            conn.setRequestProperty("Authorization", "Bearer $PUBLIC_KEY")
            conn.outputStream.use { it.write(body.toString().toByteArray()) }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream.bufferedReader().use { it.readText() }
            if (code !in 200..299) error("LeadEngine error $code: $text")
            return JSONObject(text)
        } finally {
            conn.disconnect()
        }
    }
}
