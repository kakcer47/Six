const express = require('express')
const http = require('http')
const WebSocket = require('ws')
const TelegramBot = require('node-telegram-bot-api')
const cors = require('cors')
const fs = require('fs').promises
const path = require('path')

const app = express()
const server = http.createServer(app)
const wss = new WebSocket.Server({ server })

// Environment variables
const BOT_TOKEN = '7229365201:AAHVSXlcoU06UVsTn3Vwp9deRndatnlJLVA'
const GROUP_ID = '-1002268255207'
const PORT = process.env.PORT || 3001
const WEBHOOK_URL = process.env.WEBHOOK_URL || 'https://sub-muey.onrender.com'

// Initialize Telegram bot
const bot = new TelegramBot(BOT_TOKEN)

// Middleware
app.use(cors())
app.use('/webhook', express.raw({ type: 'application/json' }))
app.use(express.json())

// ===== UNIFIED DATA STORAGE =====
let events = new Map() // id -> event (быстрый поиск)
let eventsList = [] // Отсортированный список для ленты
let lastProcessedMessageId = 0

const EVENTS_FILE = path.join(__dirname, 'events.json')
const clients = new Set()

// ===== UNIFIED EVENT FORMAT =====
function createEvent(data) {
  return {
    id: data.id || `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    title: data.title,
    description: data.description || data.content,
    author: {
      id: data.authorId || data.author?.id,
      fullName: data.author?.fullName || data.author?.name,
      username: data.author?.username,
      avatar: data.author?.avatar || data.author?.photo,
      telegramId: data.author?.telegramId
    },
    meta: {
      city: data.city || data.meta?.city || '',
      category: data.category || data.meta?.tag || '',
      gender: data.gender || data.meta?.gender || '',
      ageGroup: data.ageGroup || data.meta?.age || ''
    },
    stats: {
      likes: data.likes || data.stats?.likes || 0,
      views: data.views || data.stats?.views || 0,
      isLiked: data.isLiked || false
    },
    timestamps: {
      createdAt: data.createdAt || new Date().toISOString(),
      updatedAt: new Date().toISOString()
    },
    contacts: data.contacts || [],
    status: data.status || 'active',
    telegramMessageId: data.telegramMessageId
  }
}

// ===== DATA PERSISTENCE =====
async function loadEvents() {
  try {
    const data = await fs.readFile(EVENTS_FILE, 'utf8')
    const savedData = JSON.parse(data)
    
    events.clear()
    eventsList = []
    
    savedData.events?.forEach(eventData => {
      const event = createEvent(eventData)
      events.set(event.id, event)
      eventsList.push(event)
    })
    
    lastProcessedMessageId = savedData.lastProcessedMessageId || 0
    console.log(`📁 Loaded ${events.size} events`)
  } catch (error) {
    console.log('📁 Starting fresh - no existing data')
  }
}

async function saveEvents() {
  try {
    const data = {
      events: Array.from(events.values()),
      lastProcessedMessageId,
      updatedAt: new Date().toISOString()
    }
    await fs.writeFile(EVENTS_FILE, JSON.stringify(data, null, 2))
  } catch (error) {
    console.error('💾 Save error:', error)
  }
}

// ===== WEBSOCKET MANAGEMENT =====
wss.on('connection', (ws, req) => {
  const clientId = Date.now().toString()
  ws.clientId = clientId
  clients.add(ws)
  
  console.log(`🔗 Client connected: ${clientId} (${clients.size} total)`)
  
  ws.send(JSON.stringify({
    type: 'CONNECTED',
    data: { clientId, timestamp: Date.now() }
  }))

  ws.on('message', async (data) => {
    try {
      const message = JSON.parse(data.toString())
      await handleWebSocketMessage(message, ws)
    } catch (error) {
      console.error(`💥 WS Error from ${clientId}:`, error)
      ws.send(JSON.stringify({
        type: 'ERROR',
        data: { message: 'Failed to process message' }
      }))
    }
  })

  ws.on('close', () => {
    clients.delete(ws)
    console.log(`🔌 Client disconnected: ${clientId} (${clients.size} remaining)`)
  })

  ws.on('error', (error) => {
    console.error(`💥 WS Error ${clientId}:`, error)
    clients.delete(ws)
  })
})

function broadcast(type, data, excludeClient = null) {
  const message = JSON.stringify({ type, data })
  let sent = 0
  
  clients.forEach(client => {
    if (client !== excludeClient && client.readyState === WebSocket.OPEN) {
      try {
        client.send(message)
        sent++
      } catch (error) {
        console.error('Broadcast error:', error)
        clients.delete(client)
      }
    }
  })
  
  console.log(`📢 Broadcasted ${type} to ${sent} clients`)
}

// ===== WEBSOCKET MESSAGE HANDLERS =====
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
      senderWs.send(JSON.stringify({ type: 'PONG', data: { timestamp: Date.now() } }))
      break
    default:
      senderWs.send(JSON.stringify({
        type: 'ERROR',
        data: { message: `Unknown message type: ${type}` }
      }))
  }
}

async function handleCreateEvent(data, senderWs) {
  try {
    const event = createEvent(data)
    
    // Store event
    events.set(event.id, event)
    eventsList.unshift(event) // Newest first
    
    // Send to Telegram
    const telegramMessage = formatEventForTelegram(event)
    const sentMessage = await bot.sendMessage(GROUP_ID, telegramMessage, { parse_mode: 'HTML' })
    event.telegramMessageId = sentMessage.message_id
    
    // Save to file
    await saveEvents()
    
    // Broadcast to all clients
    broadcast('EVENT_CREATED', event, senderWs)
    
    // Respond to sender
    senderWs.send(JSON.stringify({
      type: 'CREATE_EVENT_SUCCESS',
      data: event
    }))
    
    console.log(`✅ Created: ${event.title}`)
    
  } catch (error) {
    console.error('Create error:', error)
    senderWs.send(JSON.stringify({
      type: 'CREATE_EVENT_ERROR',
      data: { message: 'Failed to create event' }
    }))
  }
}

async function handleUpdateEvent(data, senderWs) {
  try {
    const { id, ...updates } = data
    const existingEvent = events.get(id)
    
    if (!existingEvent) {
      throw new Error('Event not found')
    }
    
    // Update event
    const updatedEvent = createEvent({ ...existingEvent, ...updates })
    events.set(id, updatedEvent)
    
    // Update in list
    const listIndex = eventsList.findIndex(e => e.id === id)
    if (listIndex !== -1) {
      eventsList[listIndex] = updatedEvent
    }
    
    // Send to Telegram
    const telegramMessage = `✏️ <b>Обновлено:</b>\n\n${formatEventForTelegram(updatedEvent)}`
    await bot.sendMessage(GROUP_ID, telegramMessage, { parse_mode: 'HTML' })
    
    await saveEvents()
    broadcast('EVENT_UPDATED', updatedEvent, senderWs)
    
    senderWs.send(JSON.stringify({
      type: 'UPDATE_EVENT_SUCCESS',
      data: updatedEvent
    }))
    
    console.log(`✅ Updated: ${updatedEvent.title}`)
    
  } catch (error) {
    console.error('Update error:', error)
    senderWs.send(JSON.stringify({
      type: 'UPDATE_EVENT_ERROR',
      data: { message: error.message }
    }))
  }
}

async function handleDeleteEvent(data, senderWs) {
  try {
    const { id } = data
    const event = events.get(id)
    
    if (!event) {
      throw new Error('Event not found')
    }
    
    // Remove from storage
    events.delete(id)
    eventsList = eventsList.filter(e => e.id !== id)
    
    // Send to Telegram
    await bot.sendMessage(GROUP_ID, `🗑️ <b>Удалено событие:</b>\n\n📊 #${id}`, { parse_mode: 'HTML' })
    
    await saveEvents()
    broadcast('EVENT_DELETED', { id }, senderWs)
    
    senderWs.send(JSON.stringify({
      type: 'DELETE_EVENT_SUCCESS',
      data: { id }
    }))
    
    console.log(`✅ Deleted: ${id}`)
    
  } catch (error) {
    console.error('Delete error:', error)
    senderWs.send(JSON.stringify({
      type: 'DELETE_EVENT_ERROR',
      data: { message: error.message }
    }))
  }
}

async function handleLikeEvent(data, senderWs) {
  try {
    const { id, isLiked } = data
    const event = events.get(id)
    
    if (!event) {
      throw new Error('Event not found')
    }
    
    // Update likes
    const newLikes = isLiked 
      ? event.stats.likes + 1 
      : Math.max(0, event.stats.likes - 1)
    
    event.stats.likes = newLikes
    event.stats.isLiked = isLiked
    event.timestamps.updatedAt = new Date().toISOString()
    
    // Send to Telegram
    const action = isLiked ? 'лайкнул' : 'убрал лайк'
    await bot.sendMessage(GROUP_ID, `⚡ Событие ${action}\n\n📊 #${id} (${newLikes} лайков)`, { parse_mode: 'HTML' })
    
    await saveEvents()
    broadcast('EVENT_LIKED', { id, isLiked, likes: newLikes }, senderWs)
    
    senderWs.send(JSON.stringify({
      type: 'LIKE_EVENT_SUCCESS',
      data: { id, isLiked, likes: newLikes }
    }))
    
    console.log(`✅ Like: ${id} - ${isLiked} (${newLikes} total)`)
    
  } catch (error) {
    console.error('Like error:', error)
    senderWs.send(JSON.stringify({
      type: 'LIKE_EVENT_ERROR',
      data: { message: error.message }
    }))
  }
}

// ===== TELEGRAM FORMATTING =====
function formatEventForTelegram(event) {
  let message = `🎯 <b>${event.title}</b>\n\n${event.description}\n\n`
  
  const meta = []
  if (event.meta.city) meta.push(`📍 ${event.meta.city}`)
  if (event.meta.category) meta.push(`🏷️ ${event.meta.category}`)
  if (event.meta.gender) meta.push(`👤 ${event.meta.gender}`)
  if (event.meta.ageGroup) meta.push(`🎂 ${event.meta.ageGroup}`)
  
  if (meta.length > 0) {
    message += meta.join(' | ') + '\n\n'
  }
  
  message += `👤 ${event.author.fullName}`
  if (event.author.username) {
    message += ` (@${event.author.username})`
  }
  
  if (event.contacts?.length > 0) {
    message += '\n\n📞 Контакты:\n'
    event.contacts.forEach(contact => {
      message += `• ${contact}\n`
    })
  }
  
  message += `\n📊 #${event.id} | ⚡ ${event.stats.likes}`
  
  return message
}

// ===== HTTP API (для фронтенда) =====
app.get('/api/feed', (req, res) => {
  try {
    const {
      search, city, category, gender, ageGroup, authorId,
      sort = 'new', page = 1, limit = 20
    } = req.query

    let filtered = [...eventsList]

    // Search
    if (search) {
      const query = search.toLowerCase()
      filtered = filtered.filter(event => 
        event.title.toLowerCase().includes(query) ||
        event.description.toLowerCase().includes(query)
      )
    }

    // Filters
    if (city) filtered = filtered.filter(e => e.meta.city === city)
    if (category) filtered = filtered.filter(e => e.meta.category === category)
    if (gender) filtered = filtered.filter(e => e.meta.gender === gender)
    if (ageGroup) filtered = filtered.filter(e => e.meta.ageGroup === ageGroup)
    if (authorId) filtered = filtered.filter(e => e.author.id === authorId)

    // Sort
    if (sort === 'popularity') {
      filtered.sort((a, b) => b.stats.likes - a.stats.likes)
    } else if (sort === 'old') {
      filtered.sort((a, b) => new Date(a.timestamps.createdAt) - new Date(b.timestamps.createdAt))
    }
    // 'new' - already sorted by default

    // Pagination
    const startIndex = (page - 1) * limit
    const endIndex = startIndex + parseInt(limit)
    const paginatedEvents = filtered.slice(startIndex, endIndex)

    // Convert to frontend format
    const frontendEvents = paginatedEvents.map(event => ({
      id: event.id,
      title: event.title,
      description: event.description,
      authorId: event.author.id,
      author: {
        fullName: event.author.fullName,
        username: event.author.username,
        avatar: event.author.avatar,
        telegramId: event.author.telegramId
      },
      createdAt: event.timestamps.createdAt,
      updatedAt: event.timestamps.updatedAt,
      likes: event.stats.likes,
      isLiked: event.stats.isLiked,
      city: event.meta.city,
      category: event.meta.category,
      gender: event.meta.gender,
      ageGroup: event.meta.ageGroup,
      date: event.timestamps.createdAt,
      contacts: event.contacts,
      status: event.status
    }))

    res.json({
      posts: frontendEvents,
      hasMore: filtered.length > endIndex,
      total: filtered.length,
      page: parseInt(page),
      limit: parseInt(limit)
    })

    console.log(`📋 Feed: ${frontendEvents.length}/${filtered.length} events (page ${page})`)

  } catch (error) {
    console.error('Feed error:', error)
    res.status(500).json({ error: 'Failed to fetch feed' })
  }
})

// ===== WEBHOOK (для Telegram) =====
app.post('/webhook', (req, res) => {
  try {
    const update = JSON.parse(req.body.toString())
    
    if (update.message && 
        update.message.chat.id.toString() === GROUP_ID &&
        update.message.text &&
        update.message.message_id > lastProcessedMessageId) {
      
      console.log(`📨 Webhook: ${update.message.message_id}`)
      lastProcessedMessageId = update.message.message_id
      
      // В будущем можно добавить парсинг внешних команд
      // Пока просто логируем
    }
    
    res.status(200).send('OK')
  } catch (error) {
    console.error('Webhook error:', error)
    res.status(500).send('Error')
  }
})

// ===== HEALTH & DEBUG =====
app.get('/health', (req, res) => {
  res.json({
    status: 'OK',
    events: events.size,
    clients: clients.size,
    uptime: process.uptime()
  })
})

app.get('/api/stats', (req, res) => {
  res.json({
    totalEvents: events.size,
    connectedClients: clients.size,
    lastProcessedMessageId
  })
})

// ===== STARTUP =====
async function setupWebhook() {
  try {
    await bot.setWebHook(`${WEBHOOK_URL}/webhook`)
    console.log(`🔗 Webhook set: ${WEBHOOK_URL}/webhook`)
  } catch (error) {
    console.error('Webhook setup error:', error)
  }
}

server.listen(PORT, async () => {
  console.log(`🚀 Unified server running on port ${PORT}`)
  
  await loadEvents()
  await setupWebhook()
  
  console.log(`✅ Ready: ${events.size} events, WebSocket + HTTP API + Telegram`)
  
  // Periodic save
  setInterval(saveEvents, 5 * 60 * 1000)
})
