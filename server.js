const express = require('express')
const http = require('http')
const WebSocket = require('ws')
const TelegramBot = require('node-telegram-bot-api')
const cors = require('cors')

const app = express()
const server = http.createServer(app)
const wss = new WebSocket.Server({ server })

// Environment variables
const BOT_TOKEN = '7229365201:AAHVSXlcoU06UVsTn3Vwp9deRndatnlJLVA'
const GROUP_ID = '-1002268255207'
const PORT = process.env.PORT || 3001
const bot = new TelegramBot(BOT_TOKEN)

// Middleware
app.use(cors())
app.use(express.json())

// Store active WebSocket connections
const clients = new Set()

// WebSocket connection handler
wss.on('connection', (ws) => {
  console.log('New WebSocket connection')
  clients.add(ws)

  ws.on('close', () => {
    console.log('WebSocket connection closed')
    clients.delete(ws)
  })

  ws.on('error', (error) => {
    console.error('WebSocket error:', error)
    clients.delete(ws)
  })
})

// Broadcast to all connected clients
function broadcast(data) {
  const message = JSON.stringify(data)
  clients.forEach(client => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(message)
    }
  })
}

// Format event for Telegram
function formatEventForTelegram(event) {
  let message = `🎯 <b>${event.title}</b>\n\n`
  message += `${event.description}\n\n`
  
  if (event.city) message += `📍 ${event.city}\n`
  if (event.category) message += `🏷️ ${event.category}\n`
  if (event.gender) message += `👤 ${event.gender}\n`
  if (event.ageGroup) message += `🎂 ${event.ageGroup}\n`
  
  message += `\n👤 ${event.author.fullName}`
  if (event.author.username) {
    message += ` (@${event.author.username})`
  }
  
  return message
}

// Routes
app.post('/create', async (req, res) => {
  try {
    const event = {
      id: Date.now().toString(),
      ...req.body,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      likes: 0,
      isLiked: false
    }

    // Send to Telegram group
    const telegramMessage = formatEventForTelegram(event)
    await bot.sendMessage(GROUP_ID, telegramMessage, { parse_mode: 'HTML' })

    // Broadcast to WebSocket clients
    broadcast({
      type: 'EVENT_CREATED',
      data: event
    })

    res.json({ success: true, event })
  } catch (error) {
    console.error('Error creating event:', error)
    res.status(500).json({ error: 'Failed to create event' })
  }
})

app.post('/update', async (req, res) => {
  try {
    const { id, ...updates } = req.body
    const event = {
      ...updates,
      id,
      updatedAt: new Date().toISOString()
    }

    // Update in Telegram group (send new message)
    const telegramMessage = `✏️ <b>Updated:</b>\n\n${formatEventForTelegram(event)}`
    await bot.sendMessage(GROUP_ID, telegramMessage, { parse_mode: 'HTML' })

    // Broadcast to WebSocket clients
    broadcast({
      type: 'EVENT_UPDATED',
      data: event
    })

    res.json({ success: true, event })
  } catch (error) {
    console.error('Error updating event:', error)
    res.status(500).json({ error: 'Failed to update event' })
  }
})

app.post('/delete', async (req, res) => {
  try {
    const { id } = req.body

    // Send deletion notice to Telegram group
    await bot.sendMessage(GROUP_ID, `🗑️ <b>Event deleted</b>\n\nEvent ID: ${id}`, { parse_mode: 'HTML' })

    // Broadcast to WebSocket clients
    broadcast({
      type: 'EVENT_DELETED',
      data: { id }
    })

    res.json({ success: true })
  } catch (error) {
    console.error('Error deleting event:', error)
    res.status(500).json({ error: 'Failed to delete event' })
  }
})

app.post('/like', async (req, res) => {
  try {
    const { id, isLiked } = req.body

    // Send like update to Telegram group
    const action = isLiked ? 'liked' : 'unliked'
    await bot.sendMessage(GROUP_ID, `⚡ Event ${action}\n\nEvent ID: ${id}`, { parse_mode: 'HTML' })

    // Broadcast to WebSocket clients
    broadcast({
      type: 'EVENT_LIKED',
      data: { id, isLiked }
    })

    res.json({ success: true })
  } catch (error) {
    console.error('Error liking event:', error)
    res.status(500).json({ error: 'Failed to like event' })
  }
})

// Health check
app.get('/health', (req, res) => {
  res.json({ status: 'OK', clients: clients.size })
})

// Error handler
app.use((error, req, res, next) => {
  console.error('Server error:', error)
  res.status(500).json({ error: 'Internal server error' })
})

// Start server
server.listen(PORT, () => {
  console.log(`Bot 1 server running on port ${PORT}`)
  console.log(`WebSocket server ready`)
  console.log(`Telegram bot initialized`)
})
