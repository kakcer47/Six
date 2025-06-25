#!/usr/bin/env node
/**
 * Production Events Server with Telegram Moderation
 * =================================================
 * 
 * Architecture:
 * Frontend → Server+Bot → Group1(moderation) → Group2(publication) → Webhook → WebSocket broadcast
 * 
 * Deployment: Render.com
 * Groups: 
 * - Moderation: -1002268255207
 * - Publication: -1002361596586
 */

const express = require('express')
const http = require('http')
const WebSocket = require('ws')
const TelegramBot = require('node-telegram-bot-api')
const cors = require('cors')
const crypto = require('crypto')
const fs = require('fs')

class ProductionEventServer {
  constructor() {
    // Configuration
    this.BOT_TOKEN = process.env.BOT_TOKEN || "8059706275:AAGZGLnZfP_WvJQcqOdfRqFEJwWUF0kvmgM"
    this.MODERATION_GROUP = process.env.MODERATION_GROUP || "-1002268255207"
    this.PUBLICATION_GROUP = process.env.PUBLICATION_GROUP || "-1002361596586"
    this.WEBHOOK_SECRET = process.env.WEBHOOK_SECRET || "prod_webhook_secret_2024"
    this.PORT = process.env.PORT || 3000
    this.RENDER_URL = process.env.RENDER_EXTERNAL_URL || `https://sub-muey.onrender.com`

    // Server setup
    this.app = express()
    this.server = http.createServer(this.app)
    this.wss = new WebSocket.Server({ server: this.server })

    // Telegram Bot
    this.bot = new TelegramBot(this.BOT_TOKEN)

    // Storage
    this.events = new Map() // eventId -> event
    this.pendingEvents = new Map() // eventId -> event (waiting moderation)
    this.wsClients = new Set()
    this.cacheFile = './events_cache.json'

    // Statistics
    this.stats = {
      totalEvents: 0,
      pendingModeration: 0,
      approvedEvents: 0,
      connectedClients: 0,
      startTime: new Date().toISOString()
    }

    this.initialize()
  }

  async initialize() {
    console.log('🚀 Initializing Production Event Server...')
    console.log(`📱 Bot Token: ${this.BOT_TOKEN.substring(0, 10)}...`)
    console.log(`📋 Moderation Group: ${this.MODERATION_GROUP}`)
    console.log(`📢 Publication Group: ${this.PUBLICATION_GROUP}`)
    console.log(`🌐 Render URL: ${this.RENDER_URL}`)

    await this.setupMiddleware()
    await this.setupRoutes()
    await this.setupWebSocket()
    await this.setupTelegramBot()
    await this.loadCache()
    await this.setupWebhook()
    await this.startServer()
  }

  setupMiddleware() {
    this.app.use(cors({
      origin: ['https://telegram-events-phi.vercel.app', 'http://localhost:3000', 'http://localhost:5173'],
      credentials: true
    }))
    this.app.use(express.json({ limit: '10mb' }))
    
    // Request logging
    this.app.use((req, res, next) => {
      console.log(`📡 ${req.method} ${req.path} - ${req.ip}`)
      next()
    })

    // Health check
    this.app.get('/health', (req, res) => {
      res.json({
        status: 'healthy',
        server: 'production-events-server',
        uptime: process.uptime(),
        memory: process.memoryUsage(),
        stats: {
          ...this.stats,
          connectedClients: this.wsClients.size,
          pendingModeration: this.pendingEvents.size,
          approvedEvents: this.events.size
        },
        timestamp: new Date().toISOString()
      })
    })
  }

  setupRoutes() {
    // ==========================================
    // FRONTEND API
    // ==========================================

    // Get events feed
    this.app.get('/api/feed', (req, res) => {
      try {
        const { page = 1, limit = 20, search, city, category, authorId, view } = req.query
        const events = this.getFilteredEvents({ page: parseInt(page), limit: parseInt(limit), search, city, category, authorId, view })
        
        res.json({
          posts: events,
          hasMore: events.length === parseInt(limit),
          total: this.events.size,
          server: 'production'
        })
      } catch (error) {
        console.error('❌ Feed error:', error)
        res.status(500).json({ error: 'Failed to fetch events' })
      }
    })

    // Create event (goes to moderation)
    this.app.post('/api/events', async (req, res) => {
      try {
        const eventData = req.body
        const result = await this.createEventForModeration(eventData)
        res.json(result)
      } catch (error) {
        console.error('❌ Create event error:', error)
        res.status(500).json({ error: 'Failed to create event' })
      }
    })

    // Like event
    this.app.post('/api/events/:id/like', (req, res) => {
      try {
        const { id } = req.params
        const { isLiked } = req.body
        
        const event = this.events.get(id)
        if (!event) {
          return res.status(404).json({ error: 'Event not found' })
        }

        event.likes += isLiked ? 1 : -1
        event.likes = Math.max(0, event.likes)
        event.updatedAt = new Date().toISOString()

        this.saveCache()
        this.broadcastToClients('EVENT_LIKED', { id, isLiked, likes: event.likes })

        res.json({ success: true, likes: event.likes })
      } catch (error) {
        console.error('❌ Like error:', error)
        res.status(500).json({ error: 'Failed to like event' })
      }
    })

    // Update event (WebSocket only, but keep endpoint for compatibility)
    this.app.put('/api/events/:id', (req, res) => {
      res.json({ error: 'Use WebSocket for real-time updates' })
    })

    // Delete event (WebSocket only)
    this.app.delete('/api/events/:id', (req, res) => {
      res.json({ error: 'Use WebSocket for real-time updates' })
    })

    // ==========================================
    // TELEGRAM WEBHOOK
    // ==========================================

    this.app.post('/webhook/telegram', async (req, res) => {
      try {
        const update = req.body
        
        if (update.message && update.message.chat.id.toString() === this.PUBLICATION_GROUP) {
          await this.handlePublicationGroupMessage(update.message)
        }

        res.status(200).send('OK')
      } catch (error) {
        console.error('❌ Webhook error:', error)
        res.status(500).send('Error')
      }
    })

    // ==========================================
    // ADMIN ENDPOINTS
    // ==========================================

    this.app.get('/admin/stats', (req, res) => {
      res.json({
        ...this.stats,
        events: this.events.size,
        pending: this.pendingEvents.size,
        clients: this.wsClients.size,
        memoryUsage: process.memoryUsage(),
        uptime: process.uptime()
      })
    })

    this.app.get('/admin/events', (req, res) => {
      const events = Array.from(this.events.values()).slice(0, 50)
      res.json({ total: this.events.size, events })
    })

    this.app.post('/admin/broadcast', (req, res) => {
      const { message } = req.body
      this.broadcastToClients('ADMIN_MESSAGE', { message, timestamp: new Date().toISOString() })
      res.json({ success: true, clientsNotified: this.wsClients.size })
    })
  }

  setupWebSocket() {
    this.wss.on('connection', (ws, req) => {
      this.wsClients.add(ws)
      this.stats.connectedClients = this.wsClients.size
      console.log(`📡 Client connected from ${req.socket.remoteAddress} (${this.wsClients.size} total)`)

      // Send welcome message
      ws.send(JSON.stringify({
        type: 'CONNECTED',
        data: {
          server: 'production',
          eventsCount: this.events.size,
          timestamp: new Date().toISOString()
        }
      }))

      ws.on('message', async (data) => {
        try {
          const message = JSON.parse(data.toString())
          await this.handleWebSocketMessage(message, ws)
        } catch (error) {
          console.error('❌ WebSocket message error:', error)
          ws.send(JSON.stringify({ type: 'ERROR', error: error.message }))
        }
      })

      ws.on('close', () => {
        this.wsClients.delete(ws)
        this.stats.connectedClients = this.wsClients.size
        console.log(`📡 Client disconnected (${this.wsClients.size} remaining)`)
      })

      ws.on('error', (error) => {
        console.error('❌ WebSocket error:', error)
        this.wsClients.delete(ws)
      })
    })
  }

  async handleWebSocketMessage(message, ws) {
    const { type, data } = message

    switch (type) {
      case 'UPDATE_EVENT':
        if (data.id && this.events.has(data.id)) {
          const event = this.events.get(data.id)
          Object.assign(event, data.updates, { updatedAt: new Date().toISOString() })
          this.saveCache()
          this.broadcastToClients('EVENT_UPDATED', event)
          ws.send(JSON.stringify({ type: 'UPDATE_EVENT_SUCCESS', data: event }))
        } else {
          ws.send(JSON.stringify({ type: 'UPDATE_EVENT_ERROR', error: 'Event not found' }))
        }
        break

      case 'DELETE_EVENT':
        if (data.id && this.events.has(data.id)) {
          this.events.delete(data.id)
          this.saveCache()
          this.broadcastToClients('EVENT_DELETED', { id: data.id })
          ws.send(JSON.stringify({ type: 'DELETE_EVENT_SUCCESS', data: { id: data.id } }))
        } else {
          ws.send(JSON.stringify({ type: 'DELETE_EVENT_ERROR', error: 'Event not found' }))
        }
        break

      case 'LIKE_EVENT':
        const event = this.events.get(data.id)
        if (event) {
          event.likes += data.isLiked ? 1 : -1
          event.likes = Math.max(0, event.likes)
          this.saveCache()
          this.broadcastToClients('EVENT_LIKED', { id: data.id, isLiked: data.isLiked, likes: event.likes })
          ws.send(JSON.stringify({ type: 'LIKE_EVENT_SUCCESS', data: { likes: event.likes } }))
        } else {
          ws.send(JSON.stringify({ type: 'LIKE_EVENT_ERROR', error: 'Event not found' }))
        }
        break

      case 'GET_EVENTS':
        const events = Array.from(this.events.values()).slice(0, 20)
        ws.send(JSON.stringify({ type: 'EVENTS_LIST', data: events }))
        break

      case 'PING':
        ws.send(JSON.stringify({ type: 'PONG', data: { timestamp: Date.now() } }))
        break

      default:
        ws.send(JSON.stringify({ type: 'ERROR', error: 'Unknown message type' }))
    }
  }

  async setupTelegramBot() {
    // Handle moderation actions
    this.bot.on('callback_query', async (query) => {
      await this.handleModerationAction(query)
    })

    // Set webhook for publication group
    console.log('🤖 Telegram bot configured')
  }

  async setupWebhook() {
    try {
      const webhookUrl = `${this.RENDER_URL}/webhook/telegram`
      await this.bot.setWebHook(webhookUrl, {
        secret_token: this.WEBHOOK_SECRET
      })
      console.log(`📞 Webhook set: ${webhookUrl}`)
    } catch (error) {
      console.error('❌ Webhook setup failed:', error)
    }
  }

  // ==========================================
  // EVENT CREATION & MODERATION
  // ==========================================

  async createEventForModeration(eventData) {
    const event = {
      id: `evt_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      title: eventData.title?.trim() || 'Без названия',
      description: eventData.description?.trim() || 'Без описания',
      authorId: eventData.authorId,
      author: eventData.author || { fullName: 'Анонимный' },
      city: eventData.city || '',
      category: eventData.category || '',
      likes: 0,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      status: 'pending'
    }

    this.pendingEvents.set(event.id, event)
    this.stats.totalEvents++
    this.stats.pendingModeration++

    await this.sendToModerationGroup(event)

    console.log(`📝 Event sent for moderation: ${event.title} (${event.id})`)
    return { 
      success: true, 
      eventId: event.id, 
      status: 'pending_moderation',
      message: 'Событие отправлено на модерацию'
    }
  }

  async sendToModerationGroup(event) {
    const message = `🔍 <b>МОДЕРАЦИЯ СОБЫТИЯ</b>

📝 <b>${event.title}</b>
${event.description}

👤 Автор: ${event.author.fullName}
📍 Город: ${event.city || 'Не указан'}
📂 Категория: ${event.category || 'Не указана'}
🆔 <code>${event.id}</code>

⏰ ${new Date(event.createdAt).toLocaleString('ru-RU')}`

    const keyboard = {
      inline_keyboard: [
        [
          { text: '✅ Одобрить', callback_data: `approve_${event.id}` },
          { text: '❌ Отклонить', callback_data: `reject_${event.id}` }
        ]
      ]
    }

    try {
      await this.bot.sendMessage(this.MODERATION_GROUP, message, {
        reply_markup: keyboard,
        parse_mode: 'HTML'
      })
    } catch (error) {
      console.error('❌ Failed to send to moderation group:', error)
      throw error
    }
  }

  async handleModerationAction(query) {
    const { data, from, message } = query
    const [action, eventId] = data.split('_', 2)
    
    const event = this.pendingEvents.get(eventId)
    if (!event) {
      await this.bot.answerCallbackQuery(query.id, { text: 'Событие не найдено' })
      return
    }

    try {
      if (action === 'approve') {
        await this.approveEvent(event, from, message)
        await this.bot.answerCallbackQuery(query.id, { text: '✅ Одобрено' })
      } else if (action === 'reject') {
        await this.rejectEvent(event, from, message)
        await this.bot.answerCallbackQuery(query.id, { text: '❌ Отклонено' })
      }
    } catch (error) {
      console.error('❌ Moderation action error:', error)
      await this.bot.answerCallbackQuery(query.id, { text: 'Ошибка обработки' })
    }
  }

  async approveEvent(event, moderator, moderationMessage) {
    // Move from pending to approved
    this.pendingEvents.delete(event.id)
    event.status = 'approved'
    this.events.set(event.id, event)
    
    this.stats.pendingModeration--
    this.stats.approvedEvents++
    this.saveCache()

    // Send to publication group
    await this.sendToPublicationGroup(event)

    // Update moderation message
    await this.updateModerationMessage(moderationMessage, event, '✅ ОДОБРЕНО')

    console.log(`✅ Event approved: ${event.title} by ${moderator.username || moderator.first_name}`)
  }

  async rejectEvent(event, moderator, moderationMessage) {
    this.pendingEvents.delete(event.id)
    this.stats.pendingModeration--

    await this.updateModerationMessage(moderationMessage, event, '❌ ОТКЛОНЕНО')

    console.log(`❌ Event rejected: ${event.title} by ${moderator.username || moderator.first_name}`)
  }

  async updateModerationMessage(message, event, status) {
    try {
      const updatedText = `🔍 <b>МОДЕРАЦИЯ СОБЫТИЯ</b>

📝 <b>${event.title}</b>
${event.description}

👤 Автор: ${event.author.fullName}
📍 Город: ${event.city || 'Не указан'}
📂 Категория: ${event.category || 'Не указана'}
🆔 <code>${event.id}</code>

⏰ ${new Date(event.createdAt).toLocaleString('ru-RU')}

${status}`

      await this.bot.editMessageText(updatedText, {
        chat_id: message.chat.id,
        message_id: message.message_id,
        parse_mode: 'HTML'
      })
    } catch (error) {
      console.error('❌ Failed to update moderation message:', error)
    }
  }

  async sendToPublicationGroup(event) {
    const message = `🎯 <b>${event.title}</b>

${event.description}

👤 ${event.author.fullName}
📍 ${event.city || 'Локация не указана'}
📂 ${event.category || 'Общее'}

#event #${event.id}`

    try {
      await this.bot.sendMessage(this.PUBLICATION_GROUP, message, {
        parse_mode: 'HTML'
      })
      console.log(`📢 Published: ${event.title}`)
    } catch (error) {
      console.error('❌ Failed to send to publication group:', error)
    }
  }

  async handlePublicationGroupMessage(message) {
    try {
      const event = this.parsePublicationMessage(message)
      if (event && !this.events.has(event.id)) {
        this.events.set(event.id, event)
        this.saveCache()
        this.broadcastToClients('EVENT_CREATED', event)
        console.log(`📥 New event via webhook: ${event.title}`)
      }
    } catch (error) {
      console.error('❌ Failed to parse publication message:', error)
    }
  }

  parsePublicationMessage(message) {
    try {
      const text = message.text
      if (!text || !text.includes('#event')) return null

      const idMatch = text.match(/#([a-z0-9_]+)$/m)
      if (!idMatch) return null

      const id = idMatch[1]
      const lines = text.split('\n').filter(line => line.trim())
      
      const title = lines[0]?.replace('🎯 ', '').trim()
      const description = lines[2]?.trim()
      
      const authorLine = lines.find(line => line.startsWith('👤'))
      const cityLine = lines.find(line => line.startsWith('📍'))
      const categoryLine = lines.find(line => line.startsWith('📂'))

      return {
        id,
        title,
        description,
        author: { fullName: authorLine?.replace('👤 ', '') || 'Unknown' },
        city: cityLine?.replace('📍 ', '') || '',
        category: categoryLine?.replace('📂 ', '') || '',
        likes: 0,
        createdAt: new Date(message.date * 1000).toISOString(),
        updatedAt: new Date(message.date * 1000).toISOString(),
        status: 'approved'
      }
    } catch (error) {
      console.error('❌ Parse error:', error)
      return null
    }
  }

  // ==========================================
  // UTILITIES
  // ==========================================

  getFilteredEvents({ page, limit, search, city, category, authorId, view }) {
    let events = Array.from(this.events.values()).filter(event => event.status === 'approved')

    if (authorId) {
      events = events.filter(event => event.authorId === authorId)
    }

    if (search) {
      const searchLower = search.toLowerCase()
      events = events.filter(event =>
        event.title.toLowerCase().includes(searchLower) ||
        event.description.toLowerCase().includes(searchLower)
      )
    }

    if (city) {
      events = events.filter(event => event.city === city)
    }

    if (category) {
      events = events.filter(event => event.category === category)
    }

    events.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))

    const offset = (page - 1) * limit
    return events.slice(offset, offset + limit)
  }

  broadcastToClients(type, data) {
    const message = JSON.stringify({ type, data })
    let sentCount = 0
    
    this.wsClients.forEach(client => {
      if (client.readyState === WebSocket.OPEN) {
        client.send(message)
        sentCount++
      }
    })

    console.log(`📡 Broadcast ${type} to ${sentCount} clients`)
  }

  saveCache() {
    try {
      const cacheData = {
        events: Array.from(this.events.values()),
        stats: this.stats,
        timestamp: new Date().toISOString()
      }
      fs.writeFileSync(this.cacheFile, JSON.stringify(cacheData, null, 2))
    } catch (error) {
      console.error('❌ Failed to save cache:', error)
    }
  }

  loadCache() {
    try {
      if (fs.existsSync(this.cacheFile)) {
        const cacheData = JSON.parse(fs.readFileSync(this.cacheFile, 'utf8'))
        
        for (const event of cacheData.events || []) {
          this.events.set(event.id, event)
        }

        if (cacheData.stats) {
          this.stats = { ...this.stats, ...cacheData.stats }
        }

        console.log(`💾 Loaded ${this.events.size} events from cache`)
      }
    } catch (error) {
      console.error('❌ Failed to load cache:', error)
    }
  }

  async startServer() {
    this.server.listen(this.PORT, () => {
      console.log(`🚀 Production Event Server running on port ${this.PORT}`)
      console.log(`🌐 Health check: ${this.RENDER_URL}/health`)
      console.log(`📡 WebSocket endpoint: ws://${this.RENDER_URL}`)
      console.log(`📞 Webhook: ${this.RENDER_URL}/webhook/telegram`)
      console.log(`💾 Events in cache: ${this.events.size}`)
      console.log(`⏳ Pending moderation: ${this.pendingEvents.size}`)
    })
  }
}

// ==========================================
// PRODUCTION STARTUP
// ==========================================

const server = new ProductionEventServer()

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('🛑 SIGTERM received, shutting down gracefully')
  server.saveCache()
  process.exit(0)
})

process.on('SIGINT', () => {
  console.log('🛑 SIGINT received, shutting down gracefully')
  server.saveCache()
  process.exit(0)
})

// Keep alive for Render
setInterval(() => {
  console.log(`💓 Server alive - Events: ${server.events.size}, Clients: ${server.wsClients.size}`)
}, 300000) // 5 minutes
