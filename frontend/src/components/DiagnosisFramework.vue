<script setup lang="ts">
import Icon from './Icon.vue'
import { diagnosisDimensions } from '../diagnosisFramework'

withDefaults(defineProps<{ expanded?: boolean }>(), { expanded: false })
</script>

<template>
  <div class="diagnosis-dimensions">
    <details
      v-for="(dimension, index) in diagnosisDimensions"
      :key="dimension.id"
      class="diagnosis-dimension"
      :open="expanded"
    >
      <summary>
        <span class="diagnosis-number">0{{ index + 1 }}</span>
        <div class="diagnosis-question">
          <h4>{{ dimension.title }}</h4>
          <p>{{ dimension.question }}</p>
        </div>
        <Icon name="ChevronDown" :size="17" class="diagnosis-chevron" />
      </summary>
      <div class="diagnosis-fields">
        <section>
          <h5><Icon name="Search" :size="15" />重点问题</h5>
          <ul>
            <li v-for="item in dimension.focus" :key="item">{{ item }}</li>
          </ul>
        </section>
        <section>
          <h5><Icon name="Database" :size="15" />分析依据</h5>
          <ul>
            <li v-for="item in dimension.evidence" :key="item">{{ item }}</li>
          </ul>
        </section>
        <section>
          <h5><Icon name="ClipboardList" :size="15" />建议输出内容</h5>
          <ul>
            <li v-for="item in dimension.outputs" :key="item">{{ item }}</li>
          </ul>
        </section>
      </div>
    </details>
  </div>
</template>
