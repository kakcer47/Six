const express = require('express')
const http = require('http')
const WebSocket = require('ws')
const TelegramBot = require('node-telegram-bot-api')
const cors = require('cors')
const axios = require('axios')

const app = express()
const server = http.createServer(app)
const wss = new WebSocket.Server({ server })

// Environment variables
const BOT_TOKEN = '7229365201:AAHVSXlcoU06UVsTn3Vwp9deRndatnlJLVA'
const GROUP_ID = '-1002268255207'
const PORT = process.env.PORT || 3001
const BOT2_URL = process.env.BOT2_URL || 'https://six-z05l.onrender.com'

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

// Sync with Bot 2
async function syncWithBot2(action, data) {
  try {
    let response
    switch (action) {
      case 'create':
        response = await axios.post(`${BOT2_URL}/api/events`, data)
        break
      case 'update':
        response = await axios.put(`${BOT2_URL}/api/events/${data.id}`, data)
        break
      case 'delete':
        response = await axios.delete(`${BOT2_URL}/api/events/${data.id}`)
        break
      case 'like':
        // Implement like endpoint in Bot 2
        response = await axios.patch(`${BOT2_URL}/api/events/${data.id}/like`, { 
          isLiked: data.isLiked 
        })
        break
    }
    return response?.data
  } catch (error) {
    console.error(`Failed to sync ${action} with Bot 2:`, error.message)
    throw error
  }
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
    // First sync with Bot 2 to get the event with proper ID
    const eventData = await syncWithBot2('create', req.body)
    const event = eventData.event

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
    
    // Sync with Bot 2
    const eventData = await syncWithBot2('update', { id, ...updates })
    const event = eventData.event

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

    // Sync with Bot 2
    await syncWithBot2('delete', { id })

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

    // Sync with Bot 2
    await syncWithBot2('like', { id, isLiked })

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
  res.json({ status
