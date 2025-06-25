#!/usr/bin/env node
/**
 * Telegram Events Proxy Server
 * =============================
 * 
 * Архитектура: Сервер-прокси без хранения данных
 * Frontend → Server → Telegram Groups → Real-time response
 * 
 * Группы:
 * - Модерация: -1002268255207
 * - Публикация: -1002361596586
 */

const express = require('express')
const http = require('http')
const WebSocket = require('ws')
const TelegramBot = require('node-telegram-bot-api')
const cors = require('cors')

class TelegramEventsProxy {
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

    // WebSocket clients only
    this.wsClients = new Set()

    // Temporary storage for pending events (for moderation flow only)
    this.tempPendingEvents = new Map()

    this.initialize()
  }

  async initialize() {
    console.log('🚀 Initializing Telegram Events Proxy...')
    console.log(`📱 Bot Token: ${this.BOT_TOKEN.substring(0, 10)}...`)
    console.log(`📋 Moderation Group: ${this.MODERATION_GROUP}`)
    console.log(`📢 Publication Group: ${this.PUBLICATION_GROUP}`)
    console.log(`🌐 Server URL: ${this.RENDER_URL}`)

    await this.setupMiddleware()
    await this.setupRoutes()
    await this.setupWebSocket()
    await this.setupTelegramBot()
    await this.setupWebhook()
    await this.startServer()
  }

  setupMiddleware() {
    this.app.use(cors({
      origin: true,
      credentials: true,
      methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
      allowedHeaders: ['Content-Type', 'Authorization', 'X-Requested-With', 'User-Agent'],
      exposedHeaders: ['Content-Length', 'X-Request-ID']
    }))

    this.app.use((req, res, next) => {
      res.header('Access-Control-Allow-Origin', '*')
      res.header('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
      res.header('Access-Control-Allow-Headers', 'Content-Type, Authorization, Content-Length, X-Requested-With, User-Agent')
      
      if (req.method === 'OPTIONS') {
        res.status(200).send()
        return
      }
      next()
    })

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
        server: 'telegram-events-proxy',
        uptime: process.uptime(),
        memory: process.memoryUsage(),
        clients: this.wsClients.size,
        timestamp: new Date().toISOString()
      })
    })
  }

  setupRoutes() {
    // ==========================================
    // REAL-TIME FEED FROM TELEGRAM GROUP
    // ==========================================
    this.app.get('/api/feed', async (req, res) => {
      try {
        console.log('📖 Reading Telegram group in real-time...')

        // Читаем группу прямо сейчас
        const messages = await this.getGroupMessages()
        const events = []

        for (const message of messages) {
          const event = this.parsePublicationMessage(message)
          if (event) events.push(event)
        }

        // Фильтрация
        const { search, city, category, page = 1, limit = 20 } = req.query
        let filteredEvents = events

        if (search) {
          const searchLower = search.toLowerCase()
          filteredEvents = filteredEvents.filter(event =>
            event.title.toLowerCase().includes(searchLower) ||
            event.description.toLowerCase().includes(searchLower)
          )
        }

        if (city) filteredEvents = filteredEvents.filter(e => e.city === city)
        if (category) filteredEvents = filteredEvents.filter(e => e.category === category)

        // Сортировка по дате
        filteredEvents.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))

        // Пагинация
        const offset = (parseInt(page) - 1) * parseInt(limit)
        const paginatedEvents = filteredEvents.slice(offset, offset + parseInt(limit))

        res.json({
          posts: paginatedEvents,
          hasMore: paginatedEvents.length === parseInt(limit),
          total: filteredEvents.length,
          server: 'realtime-proxy'
        })

        console.log(`📋 Sent ${paginatedEvents.length} events from ${events.length} total`)
      } catch (error) {
        console.error('❌ Feed error:', error)
        res.status(500).json({ error: 'Failed to fetch events' })
      }
    })

    // ==========================================
    // CREATE EVENT (TO MODERATION)
    // ==========================================
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

    // ==========================================
    // TELEGRAM WEBHOOK
    // ==========================================
    this.app.post('/webhook', async (req, res) => {
      try {
        const update = req.body
        console.log('🔔 WEBHOOK RECEIVED:', JSON.stringify(update, null, 2))

        // Обработка кнопок модерации
        if (update.callback_query) {
          console.log('🔘 Processing callback_query...')
          await this.handleModerationAction(update.callback_query)
        }

        // Обработка новых сообщений в группе публикаций
        if (update.message && update.message.chat.id.toString() === this.PUBLICATION_GROUP) {
          console.log('📢 Processing publication group message...')
          await this.handlePublicationGroupMessage(update.message)
        }

        if (update.channel_post && update.channel_post.chat.id.toString() === this.PUBLICATION_GROUP) {
          console.log('📺 Processing publication channel post...')
          await this.handlePublicationGroupMessage(update.channel_post)
        }

        res.status(200).send('OK')
      } catch (error) {
        console.error('❌ Webhook error:', error)
        res.status(500).send('Error')
      }
    })

    // ==========================================
    // ADMIN
    // ==========================================
    this.app.post('/admin/broadcast', (req, res) => {
      const { message } = req.body
      this.broadcastToClients('ADMIN_MESSAGE', { message, timestamp: new Date().toISOString() })
      res.json({ success: true, clientsNotified: this.wsClients.size })
    })
  }

  setupWebSocket() {
    this.wss.on('connection', (ws, req) => {
      this.wsClients.add(ws)
      console.log(`📡 Client connected from ${req.socket.remoteAddress} (${this.wsClients.size} total)`)

      // Welcome message
      ws.send(JSON.stringify({
        type: 'CONNECTED',
        data: {
          server: 'telegram-proxy',
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
      case 'CREATE_EVENT':
        try {
          console.log('📝 WebSocket: Processing CREATE_EVENT...', data)
          const result = await this.createEventForModeration(data)
          ws.send(JSON.stringify({ type: 'CREATE_EVENT_SUCCESS', data: result }))
          console.log('✅ WebSocket: CREATE_EVENT processed successfully')
        } catch (error) {
          console.error('❌ WebSocket: CREATE_EVENT failed:', error)
          ws.send(JSON.stringify({ type: 'CREATE_EVENT_ERROR', error: error.message }))
        }
        break

      case 'PING':
        ws.send(JSON.stringify({ type: 'PONG', data: { timestamp: Date.now() } }))
        break

      default:
        console.log('❌ Unknown WebSocket message type:', type)
        ws.send(JSON.stringify({ type: 'ERROR', error: `Unknown message type: ${type}` }))
    }
  }

  async setupTelegramBot() {
    this.bot.on('callback_query', async (query) => {
      await this.handleModerationAction(query)
    })
    console.log('🤖 Telegram bot configured')
  }

  async setupWebhook() {
    try {
      const webhookUrl = `${this.RENDER_URL}/webhook`
      await this.bot.setWebHook(webhookUrl, {
        secret_token: this.WEBHOOK_SECRET
      })
      console.log(`📞 Webhook set: ${webhookUrl}`)
    } catch (error) {
      console.error('❌ Webhook setup failed:', error)
    }
  }

  // ==========================================
  // TELEGRAM GROUP READING
  // ==========================================
  async getGroupMessages() {
    try {
      const updates = await this.bot.getUpdates({ limit: 100 })

      const messages = updates
        .filter(update =>
          (update.message && update.message.chat.id.toString() === this.PUBLICATION_GROUP) ||
          (update.channel_post && update.channel_post.chat.id.toString() === this.PUBLICATION_GROUP)
        )
        .map(update => update.message || update.channel_post)
        .filter(msg => msg.text && msg.text.includes('#event'))

      console.log(`📨 Found ${messages.length} relevant messages in updates`)
      return messages
    } catch (error) {
      console.error('❌ Failed to get group messages:', error)
      return []
    }
  }

  parsePublicationMessage(message) {
    try {
      const text = message.text

      if (!text || !text.includes('#event')) {
        return null
      }

      const lines = text.split('\n').filter(line => line.trim())

      // Извлекаем данные
      const title = lines[0]?.replace(/^🎯\s*/, '').trim()
      const description = lines[1]?.trim()

      // ID события
      const lastLine = lines[lines.length - 1]
      const idMatch = lastLine?.match(/#([a-z0-9_]+)$/)
      const id = idMatch ? idMatch[1] : `auto_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`

      // Метаданные
      const authorLine = lines.find(line => line.startsWith('👤'))
      const cityLine = lines.find(line => line.startsWith('📍'))
      const categoryLine = lines.find(line => line.startsWith('📂'))

      const event = {
        id,
        title: title || 'Событие',
        description: description || 'Описание',
        author: {
          fullName: authorLine?.replace('👤 ', '').trim() || 'Unknown',
          avatar: undefined,
          username: undefined,
          telegramId: undefined
        },
        authorId: `telegram_user_${message.from?.id || 'unknown'}`,
        city: cityLine?.replace('📍 ', '').trim() || '',
        category: categoryLine?.replace('📂 ', '').trim() || '',
        gender: '',
        ageGroup: '',
        eventDate: '',
        likes: 0,
        isLiked: false,
        createdAt: new Date(message.date * 1000).toISOString(),
        updatedAt: new Date(message.date * 1000).toISOString(),
        status: 'active'
      }

      return event
    } catch (error) {
      console.error('❌ Parse error:', error)
      return null
    }
  }

  // ==========================================
  // MODERATION FLOW
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
      gender: eventData.gender || '',
      ageGroup: eventData.ageGroup || '',
      eventDate: eventData.eventDate || '',
      createdAt: new Date().toISOString(),
      status: 'pending'
    }

    // Временно сохраняем для процесса модерации
    this.tempPendingEvents.set(event.id, event)

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

    const event = this.tempPendingEvents.get(eventId)
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
    // Удаляем из временного хранения
    this.tempPendingEvents.delete(event.id)

    // Отправляем в группу публикаций
    await this.sendToPublicationGroup(event)

    // Обновляем сообщение модерации
    await this.updateModerationMessage(moderationMessage, event, '✅ ОДОБРЕНО')

    console.log(`✅ Event approved: ${event.title} by ${moderator.username || moderator.first_name}`)
  }

  async rejectEvent(event, moderator, moderationMessage) {
    this.tempPendingEvents.delete(event.id)
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

  // ==========================================
  // REAL-TIME BROADCASTS
  // ==========================================
  async handlePublicationGroupMessage(message) {
    try {
      console.log('📥 Processing publication message for broadcast...')

      const event = this.parsePublicationMessage(message)

      if (!event) {
        console.log('❌ Failed to parse event from message')
        return
      }

      console.log('🎉 New event detected, broadcasting:', event.title, event.id)

      // Отправляем broadcast всем подключенным клиентам
      this.broadcastToClients('EVENT_CREATED', event)

      console.log('📡 Event broadcasted to', this.wsClients.size, 'clients')

    } catch (error) {
      console.error('❌ Failed to handle publication message:', error)
    }
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

  // ==========================================
  // SERVER STARTUP
  // ==========================================
  async startServer() {
    this.server.listen(this.PORT, () => {
      console.log(`🚀 Telegram Events Proxy running on port ${this.PORT}`)
      console.log(`🌐 Health check: ${this.RENDER_URL}/health`)
      console.log(`📡 WebSocket endpoint: ws://${this.RENDER_URL}`)
      console.log(`📞 Webhook: ${this.RENDER_URL}/webhook`)
      console.log(`🎯 Mode: Real-time proxy without storage`)
    })
  }
}

// ==========================================
// STARTUP
// ==========================================
const server = new TelegramEventsProxy()

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('🛑 SIGTERM received, shutting down gracefully')
  process.exit(0)
})

process.on('SIGINT', () => {
  console.log('🛑 SIGINT received, shutting down gracefully')
  process.exit(0)
})

// Keep alive for Render
setInterval(() => {
  console.log(`💓 Server alive - Clients: ${server.wsClients.size}, Temp pending: ${server.tempPendingEvents.size}`)
}, 300000) // 5 minutes
