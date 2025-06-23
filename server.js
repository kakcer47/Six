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

// Initialize Telegram bot (NO POLLING!)
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

  // Listen for messages from frontend
  ws.on('message', async (data) => {
    try {
      const message = JSON.parse(data.toString())
      console.log('Received WebSocket message:', message.type)
      
      await handleWebSocketMessage(message, ws)
    } catch (error) {
      console.error('Error processing WebSocket message:', error)
      ws.send(JSON.stringify({
        type: 'ERROR',
        message: 'Failed to process message'
      }))
    }
  })

  ws.on('close', () => {
    console.log('WebSocket connection closed')
    clients.delete(ws)
  })

  ws.on('error', (error) => {
    console.error('WebSocket error:', error)
    clients.delete(ws)
  })

  // Send welcome message
  ws.send(JSON.stringify({
    type: 'CONNECTED',
    message: 'WebSocket connected successfully'
  }))
})

// Handle WebSocket messages from frontend
async function handleWebSocketMessage(message, senderWs) {
  const { type, data } = message

  switch (type) {
    case 'CREATE_EVENT':
      await handleCreateEvent(data, senderWs)
      break
      
    case 'UPDATE_EVENT':
      await handleUpdateEvent(data, senderWs)
      break
      
    case 'DELETE_EVENT':
      await handleDeleteEvent(data, senderWs)
      break
      
    case 'LIKE_EVENT':
      await handleLikeEvent(data, senderWs)
      break
      
    case 'PING':
      senderWs.send(JSON.stringify({ type: 'PONG', timestamp: Date.now() }))
      break
      
    default:
      console.log('Unknown message type:', type)
      senderWs.send(JSON.stringify({
        type: 'ERROR',
        message: `Unknown message type: ${type}`
      }))
  }
}

// Create event handler
async function handleCreateEvent(data, senderWs) {
  try {
    const event = {
      id: Date.now().toString(),
      ...data,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      likes: 0,
      isLiked: false
    }

    // Send to Telegram group
    const telegramMessage = formatEventForTelegram(event)
    await bot.sendMessage(GROUP_ID, telegramMessage, { parse_mode: 'HTML' })

    // Broadcast to all WebSocket clients
    broadcast({
      type: 'EVENT_CREATED',
      data: event
    })

    // Send success response to sender
    senderWs.send(JSON.stringify({
      type: 'CREATE_EVENT_SUCCESS',
      data: event
    }))

  } catch (error) {
    console.error('Error creating event:', error)
    senderWs.send(JSON.stringify({
      type: 'CREATE_EVENT_ERROR',
      message: 'Failed to create event'
    }))
  }
}

// Update event handler
async function handleUpdateEvent(data, senderWs) {
  try {
    const { id, ...updates } = data
    const event = {
      ...updates,
      id,
      updatedAt: new Date().toISOString()
    }

    // Send to Telegram group
    const telegramMessage = `✏️ <b>Updated:</b>\n\n${formatEventForTelegram(event)}`
    await bot.sendMessage(GROUP_ID, telegramMessage, { parse_mode: 'HTML' })

    // Broadcast to all WebSocket clients
    broadcast({
      type: 'EVENT_UPDATED',
      data: event
    })

    // Send success response
    senderWs.send(JSON.stringify({
      type: 'UPDATE_EVENT_SUCCESS',
      data: event
    }))

  } catch (error) {
    console.error('Error updating event:', error)
    senderWs.send(JSON.stringify({
      type: 'UPDATE_EVENT_ERROR',
      message: 'Failed to update event'
    }))
  }
}

// Delete event handler
async function handleDeleteEvent(data, senderWs) {
  try {
    const { id } = data

    // Send to Telegram group
    await bot.sendMessage(GROUP_ID, `🗑️ <b>Event deleted</b>\n\nEvent ID: ${id}`, { parse_mode: 'HTML' })

    // Broadcast to all WebSocket clients
    broadcast({
      type: 'EVENT_DELETED',
      data: { id }
    })

    // Send success response
    senderWs.send(JSON.stringify({
      type: 'DELETE_EVENT_SUCCESS',
      data: { id }
    }))

  } catch (error) {
    console.error('Error deleting event:', error)
    senderWs.send(JSON.stringify({
      type: 'DELETE_EVENT_ERROR',
      message: 'Failed to delete event'
    }))
  }
}

// Like event handler
async function handleLikeEvent(data, senderWs) {
  try {
    const { id, isLiked } = data

    // Send to Telegram group
    const action = isLiked ? 'liked' : 'unliked'
    await bot.sendMessage(GROUP_ID, `⚡ Event ${action}\n\nEvent ID: ${id}`, { parse_mode: 'HTML' })

    // Broadcast to all WebSocket clients
    broadcast({
      type: 'EVENT_LIKED',
      data: { id, isLiked }
    })

    // Send success response
    senderWs.send(JSON.stringify({
      type: 'LIKE_EVENT_SUCCESS',
      data: { id, isLiked }
    }))

  } catch (error) {
    console.error('Error liking event:', error)
    senderWs.send(JSON.stringify({
      type: 'LIKE_EVENT_ERROR',
      message: 'Failed to like event'
    }))
  }
}

// Broadcast to all connected clients
function broadcast(data) {
  const message = JSON.stringify(data)
  console.log(`Broadcasting to ${clients.size} clients:`, data.type)
  
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

// Health check (keep HTTP for monitoring)
app.get('/health', (req, res) => {
  res.json({ 
    status: 'OK', 
    clients: clients.size,
    uptime: process.uptime(),
    memoryUsage: process.memoryUsage()
  })
})

// WebSocket info endpoint
app.get('/ws-info', (req, res) => {
  res.json({
    connectedClients: clients.size,
    wsUrl: `ws://${req.get('host')}`,
    protocols: ['json']
  })
})

// Error handler
app.use((error, req, res, next) => {
  console.error('Server error:', error)
  res.status(500).json({ error: 'Internal server error' })
})

// Start server
server.listen(PORT, () => {
  console.log(`Bot 1 server running on port ${PORT}`)
  console.log(`WebSocket server ready - accepting connections`)
  console.log(`Telegram bot initialized - ready to send messages`)
  console.log(`Available WebSocket message types:`)
  console.log(`  - CREATE_EVENT: Create new event`)
  console.log(`  - UPDATE_EVENT: Update existing event`)
  console.log(`  - DELETE_EVENT: Delete event`)
  console.log(`  - LIKE_EVENT: Like/unlike event`)
  console.log(`  - PING: Health check`)
})
