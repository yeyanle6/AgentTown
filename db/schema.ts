import { sqliteTable, text, integer, index } from 'drizzle-orm/sqlite-core';
export const snapshots=sqliteTable('community_snapshots',{id:text('id').primaryKey(),runId:text('run_id').notNull(),payload:text('payload').notNull(),updatedAt:text('updated_at').notNull()});
export const artifacts=sqliteTable('community_artifacts',{id:text('id').primaryKey(),taskId:text('task_id').notNull(),payload:text('payload').notNull(),sha256:text('sha256').notNull(),createdAt:text('created_at').notNull()});
export const commands=sqliteTable('community_commands',{id:text('id').primaryKey(),kind:text('kind').notNull(),payload:text('payload').notNull(),status:text('status').notNull().default('pending'),result:text('result'),createdAt:text('created_at').notNull()},(table)=>[index('idx_community_commands_status').on(table.status,table.createdAt)]);

export const sources=sqliteTable('community_sources',{id:text('id').primaryKey(),name:text('name').notNull(),payload:text('payload').notNull(),sha256:text('sha256').notNull(),retrievedAt:text('retrieved_at').notNull()});
