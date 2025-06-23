const express = require('express')
const http = require('http')
const WebSocket = require('ws')
const TelegramBot = require('node-telegram-bot-api')
const cors = require('cors')
const sqlite3 = require('sqlite3').verbose()
const fs = require('fs')
const path = require('path')
const BOT2_URL = process.env.BOT2_URL || 'https://six-z05l.onrender.com'
const app = express()
const server = http.createServer(app)
const wss = new WebSocket.Server({ server })

// Environment variables
const BOT_TOKEN = '7229365201:AAHVSXlcoU06UVsTn3Vwp9deRndatnlJLVA'
const GROUP_ID = '-1002268255207'
const PORT = process.env.PORT || 3001

// Initialize Telegram bot
const bot = new TelegramBot(BOT_TOKEN)

// Middleware
app.use(cors())
app.use(express.json())

// SQLite Database
const DB_PATH = ':memory:' 
let db = null

// WebSocket clients
const clients = new Set()

app.get('/api/debug/sqlite', async (req, res) => {
  try {
    const events = await new Promise((resolve, reject) => {
      db.all('SELECT * FROM events ORDER BY created_at DESC LIMIT 10', (err, rows) => {
        if (err) reject(err)
        else resolve(rows)
      })
    })
    
    res.json({
      success: true,
      eventsInSQLite: events.length,
      events: events
    })
  } catch (error) {
    res.json({
      success: false,
      error: error.message
    })
  }
})

// Debug endpoint - проверить WebSocket клиентов
app.get('/api/debug/clients', (req, res) => {
  const clientsInfo = Array.from(clients).map(ws => ({
    id: ws.clientId,
    readyState: ws.readyState,
    readyStateText: ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'][ws.readyState]
  }))
  
  res.json({
    totalClients: clients.size,
    clients: clientsInfo
  })
})

// Test broadcast
app.post('/api/debug/test-broadcast', (req, res) => {
  const testEvent = {
    id: 'test-' + Date.now(),
    title: 'Test Event',
    description: 'This is a test event',
    authorId: 'test',
    author: { fullName: 'Test User' },
    likes: 0,
    createdAt: new Date().toISOString()
  }
  
  broadcast('EVENT_CREATED', testEvent)
  
  res.json({
    success: true,
    message: 'Test broadcast sent',
    clients: clients.size
  })
})

// ===== SQLITE SETUP =====
function initDatabase() {
  return new Promise((resolve, reject) => {
    db = new sqlite3.Database(DB_PATH, (err) => {
      if (err) {
        reject(err)
        return
      }
      
      // Create events table
      db.run(`
        CREATE TABLE events (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          description TEXT NOT NULL,
          author_id TEXT NOT NULL,
          author_name TEXT NOT NULL,
          author_username TEXT,
          author_avatar TEXT,
          city TEXT,
          category TEXT,
          gender TEXT,
          age_group TEXT,
          likes INTEGER DEFAULT 0,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          telegram_msg_id INTEGER,
          contacts TEXT,
          status TEXT DEFAULT 'active'
        )
      `, (err) => {
        if (err) {
          reject(err)
        } else {
          console.log('✅ SQLite database initialized')
          resolve()
        }
      })
    })
  })
}

// ===== EVENT OPERATIONS =====
function insertEvent(event) {
  return new Promise((resolve, reject) => {
    const sql = `
      INSERT INTO events (
        id, title, description, author_id, author_name, author_username, 
        author_avatar, city, category, gender, age_group, likes,
        created_at, updated_at, telegram_msg_id, contacts, status
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `
    
    const values = [
      event.id,
      event.title,
      event.description,
      event.authorId,
      event.author.fullName,
      event.author.username || null,
      event.author.avatar || null,
      event.city || '',
      event.category || '',
      event.gender || '',
      event.ageGroup || '',
      event.likes || 0,
      Date.now(),
      Date.now(),
      event.telegramMessageId || null,
      JSON.stringify(event.contacts || []),
      event.status || 'active'
    ]
    
    db.run(sql, values, function(err) {
      if (err) {
        reject(err)
      } else {
        resolve(event)
      }
    })
  })
}

function updateEvent(id, updates) {
  return new Promise((resolve, reject) => {
    let setParts = []
    let values = []
    
    if (updates.title) {
      setParts.push('title = ?')
      values.push(updates.title)
    }
    if (updates.description) {
      setParts.push('description = ?')
      values.push(updates.description)
    }
    if (updates.likes !== undefined) {
      setParts.push('likes = ?')
      values.push(updates.likes)
    }
    if (updates.city) {
      setParts.push('city = ?')
      values.push(updates.city)
    }
    if (updates.category) {
      setParts.push('category = ?')
      values.push(updates.category)
    }
    
    // ДОБАВЬ ЭТУ СТРОКУ:
    if (updates.telegramMessageId) {
      setParts.push('telegram_msg_id = ?')
      values.push(updates.telegramMessageId)
    }
    
    setParts.push('updated_at = ?')
    values.push(Date.now())
    values.push(id)
    
    const sql = `UPDATE events SET ${setParts.join(', ')} WHERE id = ?`
    
    db.run(sql, values, function(err) {
      if (err) {
        reject(err)
      } else {
        // Get updated event
        getEventById(id).then(resolve).catch(reject)
      }
    })
  })
}

function deleteEvent(id) {
  return new Promise((resolve, reject) => {
    db.run('DELETE FROM events WHERE id = ?', [id], function(err) {
      if (err) {
        reject(err)
      } else {
        resolve({ id, deleted: this.changes > 0 })
      }
    })
  })
}

function getEventById(id) {
  return new Promise((resolve, reject) => {
    db.get('SELECT * FROM events WHERE id = ?', [id], (err, row) => {
      if (err) {
        reject(err)
      } else {
        resolve(row ? formatEventForFrontend(row) : null)
      }
    })
  })
}

function queryEvents(filters = {}) {
  return new Promise((resolve, reject) => {
    let sql = 'SELECT * FROM events WHERE status = "active"'
    let params = []
    
    // Filters
    if (filters.search) {
      sql += ' AND (title LIKE ? OR description LIKE ?)'
      const searchTerm = `%${filters.search}%`
      params.push(searchTerm, searchTerm)
    }
    
    if (filters.city) {
      sql += ' AND city = ?'
      params.push(filters.city)
    }
    
    if (filters.category) {
      sql += ' AND category = ?'
      params.push(filters.category)
    }
    
    if (filters.gender) {
      sql += ' AND gender = ?'
      params.push(filters.gender)
    }
    
    if (filters.ageGroup) {
      sql += ' AND age_group = ?'
      params.push(filters.ageGroup)
    }
    
    if (filters.authorId) {
      sql += ' AND author_id = ?'
      params.push(filters.authorId)
    }
    
    // Sorting
    if (filters.sort === 'popularity') {
      sql += ' ORDER BY likes DESC, created_at DESC'
    } else if (filters.sort === 'old') {
      sql += ' ORDER BY created_at ASC'
    } else {
      sql += ' ORDER BY created_at DESC' // default: newest first
    }
    
    // Pagination
    if (filters.limit) {
      sql += ' LIMIT ?'
      params.push(parseInt(filters.limit))
      
      if (filters.offset) {
        sql += ' OFFSET ?'
        params.push(parseInt(filters.offset))
      }
    }
    
    db.all(sql, params, (err, rows) => {
      if (err) {
        reject(err)
      } else {
        const events = rows.map(formatEventForFrontend)
        resolve(events)
      }
    })
  })
}

function getTotalCount(filters = {}) {
  return new Promise((resolve, reject) => {
    let sql = 'SELECT COUNT(*) as count FROM events WHERE status = "active"'
    let params = []
    
    // Same filters as queryEvents
    if (filters.search) {
      sql += ' AND (title LIKE ? OR description LIKE ?)'
      const searchTerm = `%${filters.search}%`
      params.push(searchTerm, searchTerm)
    }
    
    if (filters.city) {
      sql += ' AND city = ?'
      params.push(filters.city)
    }
    
    if (filters.category) {
      sql += ' AND category = ?'
      params.push(filters.category)
    }
    
    if (filters.gender) {
      sql += ' AND gender = ?'
      params.push(filters.gender)
    }
    
    if (filters.ageGroup) {
      sql += ' AND age_group = ?'
      params.push(filters.ageGroup)
    }
    
    if (filters.authorId) {
      sql += ' AND author_id = ?'
      params.push(filters.authorId)
    }
    
    db.get(sql, params, (err, row) => {
      if (err) {
        reject(err)
      } else {
        resolve(row.count)
      }
    })
  })
}

// Format for frontend compatibility
function formatEventForFrontend(row) {
  return {
    id: row.id,
    title: row.title,
    description: row.description,
    authorId: row.author_id,
    author: {
      fullName: row.author_name,
      username: row.author_username,
      avatar: row.author_avatar,
      telegramId: row.author_id
    },
    createdAt: new Date(row.created_at).toISOString(),
    updatedAt: new Date(row.updated_at).toISOString(),
    likes: row.likes,
    isLiked: false, // TODO: implement user-specific likes
    city: row.city,
    category: row.category,
    gender: row.gender,
    ageGroup: row.age_group,
    date: new Date(row.created_at).toISOString(),
    contacts: JSON.parse(row.contacts || '[]'),
    status: row.status,
    telegramMessageId: row.telegram_msg_id
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
  let failed = 0
  
  console.log(`📢 Starting broadcast ${type} to ${clients.size} clients`)
  console.log(`📄 Message data:`, JSON.stringify(data, null, 2))
  
  clients.forEach((client) => {
    if (client !== excludeClient && client.readyState === WebSocket.OPEN) {
      try {
        client.send(message)
        sent++
        console.log(`✅ Sent to client ${client.clientId}`)
      } catch (error) {
        console.error(`💥 Failed to send to client ${client.clientId}:`, error)
        clients.delete(client)
        failed++
      }
    } else {
      console.log(`⏭️ Skipping client ${client.clientId} (state: ${client.readyState})`)
      if (client.readyState !== WebSocket.OPEN) {
        clients.delete(client)
        failed++
      }
    }
  })
  
  console.log(`📊 Broadcast result: ${sent} sent, ${failed} failed, ${clients.size} remaining`)
}

// ===== WEBSOCKET MESSAGE HANDLERS =====
async function handleWebSocketMessage(message, senderWs) {
  const { type, data } = message

  try {
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
  } catch (error) {
    console.error('WebSocket handler error:', error)
    senderWs.send(JSON.stringify({
      type: `${type}_ERROR`,
      data: { message: error.message }
    }))
  }
}

async function sendCommandToBot2(type, eventId, data = {}) {
  try {
    console.log(`📤 Sending command to BOT2: ${type} for event ${eventId}`)
    
    const response = await fetch(`${BOT2_URL}/api/webhook/command`, {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'User-Agent': 'BOT1-Service'
      },
      body: JSON.stringify({
        type,
        eventId,
        data,
        timestamp: Date.now(),
        source: 'BOT1'
      }),
      timeout: 5000
    })
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }
    
    const result = await response.json()
    console.log(`✅ BOT2 response:`, result)
    
  } catch (error) {
    console.error(`❌ Failed to send command to BOT2:`, error.message)
    // НЕ блокируем основной процесс при ошибке
  }
}

async function handleCreateEvent(data, senderWs) {
  try {
    // Create event object
    const event = {
      id: `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
      title: data.title,
      description: data.description,
      authorId: data.authorId,
      author: data.author,
      city: data.city || '',
      category: data.category || '',
      gender: data.gender || '',
      ageGroup: data.ageGroup || '',
      likes: 0,
      contacts: data.contacts || [],
      status: 'active'
    }
    
    console.log('🔄 Creating event:', event.title)
    
    // 1. Сохраняем в SQLite
    await insertEvent(event)
    console.log('✅ Saved to SQLite')
    
    // 2. Отправляем ТОЛЬКО событие в Telegram группу
    const telegramMessage = formatEventForTelegram(event)
    const sentMessage = await bot.sendMessage(GROUP_ID, telegramMessage, { parse_mode: 'HTML' })
    event.telegramMessageId = sentMessage.message_id
    console.log('✅ Sent to Telegram group:', sentMessage.message_id)
    
    // 3. Обновляем telegramMessageId в SQLite
    await updateEvent(event.id, { telegramMessageId: sentMessage.message_id })
    
    // 4. Уведомляем BOT2 о новом событии (вместо спама в группу)
    await sendCommandToBot2('NEW_EVENT', event.id, {
      event: {
        ...event,
        telegramMessageId: sentMessage.message_id,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString()
      }
    })
    
    // 5. Broadcast to WebSocket clients
    const frontendEvent = formatEventForFrontend({
      id: event.id,
      title: event.title,
      description: event.description,
      author_id: event.authorId,
      author_name: event.author.fullName,
      author_username: event.author.username,
      author_avatar: event.author.avatar,
      city: event.city,
      category: event.category,
      gender: event.gender,
      age_group: event.ageGroup,
      likes: event.likes,
      created_at: Date.now(),
      updated_at: Date.now(),
      telegram_msg_id: sentMessage.message_id,
      contacts: JSON.stringify(event.contacts),
      status: event.status
    })
    
    broadcast('EVENT_CREATED', frontendEvent)
    
    // 6. Respond to sender
    senderWs.send(JSON.stringify({
      type: 'CREATE_EVENT_SUCCESS',
      data: frontendEvent
    }))
    
    console.log(`✅ Event created successfully: ${event.title}`)
    
  } catch (error) {
    console.error('❌ Create event error:', error)
    senderWs.send(JSON.stringify({
      type: 'CREATE_EVENT_ERROR',
      data: { message: 'Failed to create event: ' + error.message }
    }))
  }
}

async function handleUpdateEvent(data, senderWs) {
  const { id, ...updates } = data
  
  try {
    // Update in SQLite
    const updatedEvent = await updateEvent(id, updates)
    
    if (!updatedEvent) {
      throw new Error('Event not found')
    }
    
    // ❌ УБРАЛИ: Отправку в Telegram группу
    // await bot.sendMessage(GROUP_ID, `✏️ <b>Обновлено:</b>\n\n${formatEventForTelegram(updatedEvent)}`, { parse_mode: 'HTML' })
    
    // ✅ ДОБАВИЛИ: Команду BOT2
    await sendCommandToBot2('UPDATE_EVENT', id, {
      updates,
      updatedEvent
    })
    
    // Broadcast to WebSocket clients
    broadcast('EVENT_UPDATED', updatedEvent, senderWs)
    
    senderWs.send(JSON.stringify({
      type: 'UPDATE_EVENT_SUCCESS',
      data: updatedEvent
    }))
    
    console.log(`✅ Updated: ${updatedEvent.title}`)
    
  } catch (error) {
    console.error('❌ Update event error:', error)
    senderWs.send(JSON.stringify({
      type: 'UPDATE_EVENT_ERROR',
      data: { message: 'Failed to update event: ' + error.message }
    }))
  }
}

async function handleDeleteEvent(data, senderWs) {
  const { id } = data
  
  try {
    // Get event before deletion (for BOT2)
    const event = await getEventById(id)
    
    // Delete from SQLite
    const result = await deleteEvent(id)
    
    if (!result.deleted) {
      throw new Error('Event not found')
    }
    
    // ❌ УБРАЛИ: Спам в группу
    // await bot.sendMessage(GROUP_ID, `🗑️ <b>Удалено событие:</b>\n\n📊 #${id}`, { parse_mode: 'HTML' })
    
    // ✅ ДОБАВИЛИ: Команду BOT2
    await sendCommandToBot2('DELETE_EVENT', id, {
      deletedEvent: event
    })
    
    // Broadcast to WebSocket clients
    broadcast('EVENT_DELETED', { id }, senderWs)
    
    senderWs.send(JSON.stringify({
      type: 'DELETE_EVENT_SUCCESS',
      data: { id }
    }))
    
    console.log(`✅ Deleted: ${id}`)
    
  } catch (error) {
    console.error('❌ Delete event error:', error)
    senderWs.send(JSON.stringify({
      type: 'DELETE_EVENT_ERROR',
      data: { message: 'Failed to delete event: ' + error.message }
    }))
  }
}

async function handleLikeEvent(data, senderWs) {
  const { id, isLiked } = data
  
  try {
    // Get current event
    const event = await getEventById(id)
    if (!event) {
      throw new Error('Event not found')
    }
    
    // Update likes
    const newLikes = isLiked 
      ? event.likes + 1 
      : Math.max(0, event.likes - 1)
    
    await updateEvent(id, { likes: newLikes })
    
    // ❌ УБРАЛИ: Спам в группу
    // const action = isLiked ? 'лайкнул' : 'убрал лайк'
    // await bot.sendMessage(GROUP_ID, `⚡ Событие ${action}\n\n📊 #${id} (${newLikes} лайков)`, { parse_mode: 'HTML' })
    
    // ✅ ДОБАВИЛИ: Команду BOT2
    await sendCommandToBot2('UPDATE_LIKES', id, {
      isLiked,
      likes: newLikes,
      previousLikes: event.likes
    })
    
    // Broadcast to WebSocket clients
    broadcast('EVENT_LIKED', { id, isLiked, likes: newLikes }, senderWs)
    
    senderWs.send(JSON.stringify({
      type: 'LIKE_EVENT_SUCCESS',
      data: { id, isLiked, likes: newLikes }
    }))
    
    console.log(`✅ Like: ${id} - ${isLiked} (${newLikes} total)`)
    
  } catch (error) {
    console.error('❌ Like event error:', error)
    senderWs.send(JSON.stringify({
      type: 'LIKE_EVENT_ERROR',
      data: { message: 'Failed to like event: ' + error.message }
    }))
  }
}

// ===== TELEGRAM FORMATTING =====
function formatEventForTelegram(event) {
  // Чистое сообщение события без служебной информации
  let message = `🎯 <b>${event.title}</b>\n\n${event.description}\n\n`
  
  const meta = []
  if (event.city) meta.push(`📍 ${event.city}`)
  if (event.category) meta.push(`🏷️ ${event.category}`)
  if (event.gender) meta.push(`👤 ${event.gender}`)
  if (event.ageGroup) meta.push(`🎂 ${event.ageGroup}`)
  
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
  
  // Только ID и начальные лайки, обновления через BOT2
  message += `\n📊 #${event.id} | ⚡ ${event.likes || 0}`
  
  return message
}

// ===== HTTP API =====
app.get('/api/feed', async (req, res) => {
  try {
    const {
      search, city, category, gender, ageGroup, authorId,
      sort = 'new', page = 1, limit = 20
    } = req.query

    const filters = {
      search, city, category, gender, 
      ageGroup, authorId, sort,
      limit: parseInt(limit),
      offset: (parseInt(page) - 1) * parseInt(limit)
    }

    // Get events and total count
    const [events, totalCount] = await Promise.all([
      queryEvents(filters),
      getTotalCount(filters)
    ])

    const hasMore = filters.offset + events.length < totalCount

    res.json({
      posts: events,
      hasMore,
      total: totalCount,
      page: parseInt(page),
      limit: parseInt(limit)
    })

    console.log(`📋 Feed: ${events.length}/${totalCount} events (page ${page})`)

  } catch (error) {
    console.error('Feed error:', error)
    res.status(500).json({ error: 'Failed to fetch feed' })
  }
})

app.get('/health', async (req, res) => {
  let bot2Status = 'unknown'
  
  try {
    const response = await fetch(`${BOT2_URL}/health`, { timeout: 3000 })
    bot2Status = response.ok ? 'connected' : 'error'
  } catch (error) {
    bot2Status = 'disconnected'
  }
  
  res.json({
    status: 'OK',
    service: 'BOT1 - Event Creator',
    clients: clients.size,
    uptime: process.uptime(),
    bot2Status,
    bot2Url: BOT2_URL
  })
})

// ===== STARTUP =====
server.listen(PORT, async () => {
  console.log(`🚀 BOT1 Event Creator running on port ${PORT}`)
  console.log(`📝 Creates events in group: ${GROUP_ID}`)
  console.log(`🔗 Commands to BOT2: ${BOT2_URL}`)
  
  try {
    await initDatabase()
    
    // Проверяем связь с BOT2
    try {
      const response = await fetch(`${BOT2_URL}/health`, { timeout: 5000 })
      if (response.ok) {
        console.log('✅ BOT2 connection verified')
      } else {
        console.log('⚠️ BOT2 not responding, commands will fail silently')
      }
    } catch (error) {
      console.log('⚠️ BOT2 not available, commands will fail silently')
    }
    
    console.log(`✅ Ready: Clean event creation without group spam`)
    
  } catch (error) {
    console.error('❌ Startup error:', error)
    process.exit(1)
  }
})
