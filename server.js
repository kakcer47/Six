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
wss.on('connection', (ws, req) => {
  const clientId = Date.now().toString()
  const clientIP = req.socket.remoteAddress
  
  console.log(`🔗 New WebSocket connection - ID: ${clientId}, IP: ${clientIP}`)
  console.log(`👥 Total clients: ${clients.size + 1}`)
  
  ws.clientId = clientId
  clients.add(ws)

  // Send welcome message with client info
  ws.send(JSON.stringify({
    type: 'CONNECTED',
    message: 'WebSocket connected successfully',
    clientId: clientId,
    timestamp: Date.now()
  }))

  // Heartbeat mechanism
  ws.isAlive = true
  ws.on('pong', () => {
    ws.isAlive = true
  })

  // Listen for messages from frontend
  ws.on('message', async (data) => {
    try {
      const message = JSON.parse(data.toString())
      console.log(`📨 Message from client ${clientId}:`, message.type)
      
      // Handle ping manually
      if (message.type === 'PING') {
        ws.send(JSON.stringify({ type: 'PONG', timestamp: Date.now() }))
        return
      }
      
      await handleWebSocketMessage(message, ws)
    } catch (error) {
      console.error(`💥 Error processing message from client ${clientId}:`, error)
      ws.send(JSON.stringify({
        type: 'ERROR',
        message: 'Failed to process message'
      }))
    }
  })

  ws.on('close', (code, reason) => {
    console.log(`🔌 WebSocket disconnected - ID: ${clientId}, Code: ${code}, Reason: ${reason}`)
    clients.delete(ws)
    console.log(`👥 Remaining clients: ${clients.size}`)
  })

  ws.on('error', (error) => {
    console.error(`💥 WebSocket error - ID: ${clientId}:`, error)
    clients.delete(ws)
  })
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
      t: "post",
      id: `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
      title: data.title,
      content: data.description,
      author: {
        id: data.authorId,
        name: data.author.fullName,
        photo: data.author.avatar,
        username: data.author.username
      },
      meta: {
        city: data.city || '',
        tag: data.category || '',
        gender: data.gender || '',
        age: data.ageGroup || '',
        date: new Date().toLocaleDateString('ru-RU', { 
          day: '2-digit', 
          month: '2-digit' 
        })
      },
      ts: Date.now(),
      stats: {
        likes: 0,
        views: 0,
        last_updated: Date.now()
      },
      contacts: data.contacts,
      status: 'active'
    }

    // Send to Telegram group
    const telegramMessage = formatEventForTelegram(event)
    await bot.sendMessage(GROUP_ID, telegramMessage, { parse_mode: 'HTML' })

    broadcast({
      type: 'EVENT_CREATED',
      data: event
    })

    // Send success response to sender
    senderWs.send(JSON.stringify({
      type: 'CREATE_EVENT_SUCCESS',
      data: event
    }))

    console.log(`✅ Event created and sent to Telegram: ${event.title}`)

  } catch (error) {
    console.error('Error creating event:', error)
    senderWs.send(JSON.stringify({
      type: 'CREATE_EVENT_ERROR',
      message: 'Failed to create event'
    }))
  }
}

async function handleUpdateEvent(data, senderWs) {
  try {
    const { id, ...updates } = data
    const event = {
      ...updates,
      id,
      stats: {
        ...updates.stats,
        last_updated: Date.now()
      }
    }

    // Send to Telegram group
    const telegramMessage = `✏️ <b>Обновлено:</b>\n\n${formatEventForTelegram(event)}`
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

// Like event handler
async function handleLikeEvent(data, senderWs) {
  try {
    const { id, isLiked } = data
    
    // Увеличиваем/уменьшаем лайки
    const newLikes = isLiked ? 1 : -1 // Simplified, в реальности нужна база данных
    
    const eventData = {
      id,
      isLiked,
      likes: Math.max(0, newLikes), // Не даем уйти в минус
      stats: {
        last_updated: Date.now()
      }
    }

    // Send to Telegram group
    const action = isLiked ? 'лайкнул' : 'убрал лайк'
    await bot.sendMessage(GROUP_ID, `⚡ Событие ${action}\n\n📊 ID: #${id}`, { parse_mode: 'HTML' })

    // Broadcast to all WebSocket clients
    broadcast({
      type: 'EVENT_LIKED',
      data: eventData
    })

    // Send success response
    senderWs.send(JSON.stringify({
      type: 'LIKE_EVENT_SUCCESS',
      data: eventData
    }))

  } catch (error) {
    console.error('Error liking event:', error)
    senderWs.send(JSON.stringify({
      type: 'LIKE_EVENT_ERROR',
      message: 'Failed to like event'
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
  const activeClients = Array.from(clients).filter(client => client.readyState === WebSocket.OPEN)
  
  console.log(`📢 Broadcasting to ${activeClients.length}/${clients.size} clients:`, data.type)
  console.log(`💬 Message:`, JSON.stringify(data, null, 2))
  
  let successCount = 0
  let errorCount = 0
  
  clients.forEach((client, index) => {
    try {
      if (client.readyState === WebSocket.OPEN) {
        client.send(message)
        successCount++
        console.log(`✅ Sent to client ${index + 1}`)
      } else {
        console.log(`❌ Client ${index + 1} not ready (state: ${client.readyState})`)
        clients.delete(client) // Remove dead connections
      }
    } catch (error) {
      console.error(`💥 Error sending to client ${index + 1}:`, error)
      clients.delete(client)
      errorCount++
    }
  })
  
  console.log(`📊 Broadcast result: ${successCount} success, ${errorCount} errors, ${clients.size} remaining`)
}

function formatEventForTelegram(event) {
  let message = `🎯 <b>${event.title}</b>\n\n`
  message += `${event.content}\n\n`
  
  const meta = []
  if (event.meta.city) meta.push(`📍 ${event.meta.city}`)
  if (event.meta.tag) meta.push(`🏷️ ${event.meta.tag}`)
  if (event.meta.gender) meta.push(`👤 ${event.meta.gender}`)
  if (event.meta.age) meta.push(`🎂 ${event.meta.age}`)
  
  if (meta.length > 0) {
    message += meta.join(' | ') + '\n\n'
  }
  
  message += `👤 ${event.author.name}`
  if (event.author.username) {
    message += ` (@${event.author.username})`
  }
  
  if (event.contacts && event.contacts.length > 0) {
    message += '\n\n📞 Контакты:\n'
    event.contacts.forEach(contact => {
      message += `• ${contact}\n`
    })
  }
  
  message += `\n📊 #${event.id}`
  
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
  const clientsInfo = Array.from(clients).map((ws, index) => ({
    id: ws.clientId || `client-${index}`,
    readyState: ws.readyState,
    isAlive: ws.isAlive,
    readyStateText: ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'][ws.readyState]
  }))

  res.json({
    connectedClients: clients.size,
    activeClients: Array.from(clients).filter(ws => ws.readyState === WebSocket.OPEN).length,
    wsUrl: `ws://${req.get('host')}`,
    protocols: ['json'],
    clients: clientsInfo
  })
})

// Debugging endpoint to manually test broadcast
app.post('/test-broadcast', (req, res) => {
  const testMessage = {
    type: 'TEST_BROADCAST',
    data: {
      message: 'This is a test broadcast',
      timestamp: Date.now()
    }
  }
  
  broadcast(testMessage)
  
  res.json({
    success: true,
    message: 'Test broadcast sent',
    clientsCount: clients.size
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
  
  // Heartbeat interval to check client connections
  setInterval(() => {
    console.log(`💓 Heartbeat check - ${clients.size} clients`)
    
    clients.forEach((ws) => {
      if (ws.isAlive === false) {
        console.log(`💀 Terminating dead client: ${ws.clientId}`)
        ws.terminate()
        clients.delete(ws)
        return
      }
      
      ws.isAlive = false
      ws.ping()
    })
    
    console.log(`💓 After cleanup: ${clients.size} active clients`)
  }, 30000) // Check every 30 seconds
})
