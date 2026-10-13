import { theme, type ThemeConfig } from 'antd'

export const appTheme: ThemeConfig = {
  algorithm: theme.defaultAlgorithm,
  token: {
    colorPrimary: '#0f8b6f',
    colorInfo: '#0f8b6f',
    colorSuccess: '#19744f',
    colorWarning: '#b86f1c',
    colorError: '#c4473d',
    colorBgContainer: 'rgba(255, 255, 255, 0.72)',
    colorBorderSecondary: 'rgba(31, 49, 48, 0.14)',
    borderRadius: 14,
    fontFamily: '"Space Grotesk","Noto Sans SC","Segoe UI",sans-serif',
  },
  components: {
    Layout: {
      siderBg: 'rgba(244, 251, 248, 0.82)',
      bodyBg: 'transparent',
      headerBg: 'transparent',
    },
    Card: {
      borderRadiusLG: 18,
    },
    Menu: {
      itemBorderRadius: 10,
      itemSelectedBg: 'rgba(15, 139, 111, 0.14)',
      itemSelectedColor: '#0a6b54',
      itemColor: '#355553',
    },
    Button: {
      defaultBorderColor: 'rgba(31, 49, 48, 0.18)',
    },
    Table: {
      headerBg: 'rgba(245, 252, 249, 0.88)',
    },
    Tag: {
      defaultColor: '#1f3130',
    },
  },
}

